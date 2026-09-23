"""Assemble candidates + answers into a product, with per-field confidence
and a bigger-model fallback for low-confidence fields only.
"""

import json
import os
import re
import time

import httpx
from pydantic import BaseModel

from .ask import Answer, Question, ask
from .sweep import Candidates, sweep
from .taxonomy import shortlist

CONFIDENCE_FLOOR = float(os.environ.get("PLUCK_FLOOR", "0.85"))
FALLBACK_MODEL = os.environ.get("PLUCK_FALLBACK", "google/gemini-2.5-flash-lite")


class Field(BaseModel):
    value: str | float | None
    confidence: float
    source: str  # "choice" | "fallback" | "none"


class Product(BaseModel):
    name: Field
    price: Field
    compare_at: Field
    currency: Field
    category: Field
    images: list[str]
    meta: dict


def _amount(candidate: str | None) -> float | None:
    if not candidate:
        return None
    try:
        return float(candidate.split("  [")[0])
    except ValueError:
        return None


def _questions(c: Candidates) -> list[Question]:
    qs = [
        Question("name", "Which is the product's concise name (not a long page/SEO title)?", c.names, allow_none=False),
        Question("price", "Which number is the price a buyer pays right now (after any sale or instant savings)?", c.prices),
        Question("compare_at",
                 "Which number is the crossed-out, 'was', or list price shown alongside the current price? N if the page shows no higher original price.", c.prices),
    ]
    if c.currencies:
        qs.append(Question("currency", "Which currency are the prices in?", c.currencies,
                           allow_none=False))
    if c.categories:
        qs.append(Question("category", "Which category best fits this product? Prefer ordinary retail categories over specialty/ceremonial ones.", c.categories))
    for i, img in enumerate(c.images[:8]):
        qs.append(Question(f"img{i}", f"Is this a photo of the product itself? {img}",
                           ["yes", "no"], allow_none=False))
    return qs


def _state(c: Candidates) -> str:
    parts = [f"Page title: {c.title}"]
    if c.breadcrumb:
        parts.append(f"Breadcrumb: {c.breadcrumb}")
    return "\n".join(parts)


async def _fallback(html: str, fields: list[str], categories: list[str]) -> dict:
    """Re-ask only the failed fields with a generative model over page text."""
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)[:12_000]
    schema = {f: "string or number or null" for f in fields}
    cat_rule = (f" For 'category' you MUST copy one string verbatim from this list "
                f"(or null): {categories}" if "category" in fields else "")
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPEN_ROUTER_API_KEY']}"},
            json={
                "model": FALLBACK_MODEL,
                "messages": [
                    {"role": "system", "content":
                     "Extract the requested product fields from the page text. "
                     f"Reply with a JSON object with exactly these keys: {list(schema)}. "
                     "Use null when the page does not state a value." + cat_rule},
                    {"role": "user", "content": text},
                ],
                "response_format": {"type": "json_object"},
            },
        )
    r.raise_for_status()
    try:
        return json.loads(r.json()["choices"][0]["message"]["content"])
    except (KeyError, json.JSONDecodeError):
        return {}


async def extract(html: str) -> Product:
    t0 = time.time()
    c = sweep(html)
    c.categories = shortlist(" ".join(c.names[:2]) + " " + c.breadcrumb, k=8)[:12]

    answers, usage = await ask(_state(c), _questions(c))

    def field(key: str, options: list[str], transform=lambda v: v) -> Field:
        a: Answer | None = answers.get(key)
        if a is None:
            return Field(value=None, confidence=0.0, source="none")
        picked = a.pick(options)
        return Field(value=transform(picked) if picked else None,
                     confidence=round(a.probability, 3),
                     source="choice")

    out = {
        "name": field("name", c.names),
        "price": field("price", c.prices, _amount),
        "compare_at": field("compare_at", c.prices, _amount),
        "currency": field("currency", c.currencies),
        "category": field("category", c.categories),
    }
    images = [c.images[i].split(" | ")[0] for i in range(min(8, len(c.images)))
              if (a := answers.get(f"img{i}")) and a.letter == "A" and a.probability >= 0.6]

    # confidence-routed fallback: only fields below the floor (compare_at may
    # honestly be absent, so a low-confidence N is not retried)
    weak = [k for k, f in out.items() if f.confidence < CONFIDENCE_FLOOR]
    # a product page always has a price; a confident "none" is still a miss
    if out["price"].value is None and "price" not in weak:
        weak.append("price")
    if weak:
        fb = await _fallback(html, weak, c.categories)
        for k in weak:
            if fb.get(k) is not None:
                v = fb[k]
                if k in ("price", "compare_at"):
                    try:
                        v = float(re.sub(r"[^\d.]", "", str(v)))
                    except ValueError:
                        continue
                if k == "category" and v not in c.categories:
                    continue  # only taxonomy strings count
                out[k] = Field(value=v, confidence=0.5, source="fallback")
            elif k == "compare_at" and out[k].value is not None:
                out[k] = Field(value=None, confidence=0.5, source="fallback")

    # a compare-at equal to the price means "not on sale"
    if out["compare_at"].value is not None and out["price"].value is not None \
            and abs(out["compare_at"].value - out["price"].value) < 0.01:
        out["compare_at"] = Field(value=None, confidence=out["compare_at"].confidence,
                                  source=out["compare_at"].source)
    sym_map = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "kr": "SEK"}
    if isinstance(out["currency"].value, str):
        out["currency"].value = sym_map.get(out["currency"].value.strip(), out["currency"].value.strip().upper())

    return Product(
        **out,
        images=images,
        meta={
            "latency_s": round(time.time() - t0, 2),
            "chooser_tokens": usage,
            "fallback_fields": weak,
            "n_candidates": {"prices": len(c.prices), "names": len(c.names),
                             "images": len(c.images)},
        },
    )
