"""The decision tree: climb rungs only as far as the page makes you.

Most pages answer at rung one with their own declared data. The model is the
last resort, not the pipeline.
"""

import asyncio
import re
import time

from pydantic import BaseModel

from . import mine, rungs, taxonomy
from .infer import infer, pick_leaf

_CORE = ("name", "price", "currency")
_SALE = re.compile(r"was \$|% off|you save|original price|compare at|-\d+%", re.I)
_SYM = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "kr": "SEK"}


class Field(BaseModel):
    value: str | float | None
    source: str  # declared | shipped | computed | inferred | none


class Product(BaseModel):
    name: Field
    price: Field
    compare_at: Field
    currency: Field
    category: Field
    images: list[str]
    meta: dict


def _hint(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]{3,150})", html, re.I) \
        or re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']{3,150})', html, re.I)
    return mine._unesc(m.group(1)) if m else ""


_VIS = re.compile(r"[$€£¥]\s?(\d[\d.,]*)|(\d[\d.,]*)\s?(?:[$€£¥]|USD|EUR|GBP|kr\b)")


def _visible_prices(html: str) -> set[float]:
    text = re.sub(r"<(script|style|svg|noscript)[\s\S]*?</\1>|<[^>]+>", " ", html)
    out = set()
    for m in _VIS.finditer(text):
        v = mine._num(m.group(1) or m.group(2))
        if v is not None and 0.5 <= v <= 500_000:
            out.add(v)
    return out


async def extract(html: str) -> Product:
    t0 = time.time()
    scr = rungs.scripts(html)
    hint = _hint(html)
    fields: dict[str, Field] = {}
    extras: dict = {"crumbs": [], "images": []}
    conflicts: set[str] = set()

    def merge(found: dict, source: str):
        for k, v in found.items():
            if k in ("crumbs", "images"):
                extras[k] += v if isinstance(v, list) else [v]
            elif k == "conflict":
                conflicts.add("price")
            elif k not in fields and v is not None:
                fields[k] = Field(value=v, source=source)
            elif k in fields and isinstance(v, (int, float)) \
                    and isinstance(fields[k].value, (int, float)) \
                    and abs(v - fields[k].value) > 0.01 * max(v, fields[k].value):
                conflicts.add(k)  # two rungs disagree: the model referees

    # rungs a+b are free: parse what the page already carries
    merge(mine.jsonld(rungs.declared(scr)), "declared")
    merge(mine.state(rungs.shipped(scr), hint), "shipped")

    # rung c: only run the page's js when the cheap rungs came up short
    need = [k for k in _CORE if k not in fields]
    if need:  # v8 blocks, so keep it off the event loop
        merge(mine.state(await asyncio.to_thread(rungs.computed, scr), hint), "computed")

    if "name" not in fields and hint:
        merge({"name": hint.split("|")[0].strip()}, "declared")

    # a deterministic price the visible page never shows is suspect: referee it
    if "price" in fields:
        vis = _visible_prices(html)
        p = float(fields["price"].value)
        if vis and not any(abs(p - v) <= 0.011 * max(p, v) for v in vis):
            conflicts.add("price")

    # rung d: one generative call for the leftovers; category rides along free
    missing = [k for k in _CORE if k not in fields] + sorted(conflicts)
    if "compare_at" not in fields and "compare_at" not in missing and _SALE.search(html):
        missing.append("compare_at")
    known = str(fields["name"].value) if "name" in fields else ""
    if extras["crumbs"]:
        known += " | " + " > ".join(dict.fromkeys(str(c) for c in extras["crumbs"][:5]))
    desc = re.search(r'(?:og|name=["\'])[:"\']description["\'][^>]*content=["\']([^"\']{20,300})', html, re.I)
    if desc:
        known += " | " + mine._unesc(desc.group(1))
    fb, usage = await infer(html, missing, taxonomy.TOPS, known)
    for k in missing:
        v = fb.get(k)
        if k in ("price", "compare_at") and v is not None:
            v = mine._num(v)
        if v is not None or k not in fields:
            fields[k] = Field(value=v, source="inferred" if v is not None else "none")

    # category descends the taxonomy like everything else climbed the page:
    # top-level branch first, then one pick inside that real subtree
    cat = None
    if top := taxonomy.top(fb.get("category")):
        leaf, usage2 = await pick_leaf(known, html, taxonomy.subtree(top))
        usage = {k: (usage.get(k) or 0) + (usage2.get(k) or 0) for k in usage | usage2
                 if isinstance(usage.get(k, usage2.get(k)), (int, float))}
        cat = taxonomy.snap(leaf)
    fields["category"] = Field(value=cat, source="inferred" if cat else "none")

    for k in ("name", "price", "compare_at", "currency", "category"):
        fields.setdefault(k, Field(value=None, source="none"))

    # a compare-at equal to the price means "not on sale"
    p, ca = fields["price"].value, fields["compare_at"].value
    if p is not None and ca is not None and abs(float(ca) - float(p)) < 0.01:
        fields["compare_at"] = Field(value=None, source=fields["compare_at"].source)
    cur = fields["currency"].value
    if isinstance(cur, str):
        fields["currency"].value = _SYM.get(cur.strip(), cur.strip().upper())

    return Product(
        **fields,
        images=[str(u) for u in dict.fromkeys(extras["images"]) if str(u).startswith("http")][:10],
        meta={
            "latency_s": round(time.time() - t0, 2),
            "llm_fields": missing + ["category"],
            "llm_tokens": usage,
            "sources": {k: f.source for k, f in fields.items()},
        },
    )
