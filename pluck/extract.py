# the router: one page in, one product out
# climbs four sources cheapest first and stops once name, price and currency are filled
#   extract          walk the rungs, referee disputes, let the model fill the rest
#   _Fields          first rung to answer a field wins, disagreements become disputes
#   _visible_prices  a deterministic price must show up on the rendered page
#   _category        taxonomy descent: pick the top level branch then one path inside it

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


class _Fields:
    def __init__(self):
        self.data: dict[str, Field] = {}
        self.crumbs: list = []
        self.images: list = []
        self.disputes: set[str] = set()

    def merge(self, found: dict, source: str):
        for k, v in found.items():
            if k == "crumbs":
                self.crumbs += v
            elif k == "images":
                self.images += v
            elif k == "conflict":
                # the page itself declared several prices, that is a dispute
                self.disputes.add("price")
            elif k not in self.data and v is not None:
                self.data[k] = Field(value=v, source=source)
            elif k in self.data and _differ(v, self.data[k].value):
                # two rungs disagree, the model referees
                self.disputes.add(k)

    def set(self, k: str, v, source: str):
        self.data[k] = Field(value=v, source=source)

    def value(self, k: str):
        return self.data[k].value if k in self.data else None

    def core_missing(self) -> list[str]:
        return [k for k in _CORE if k not in self.data]


def _differ(a, b) -> bool:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(a - b) > 0.01 * max(a, b)


def _hint(html: str) -> str:
    # page title, used to pick the right product out of state blobs
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


def _context(f: _Fields, html: str) -> str:
    # name plus breadcrumbs plus meta description, the identity the model sees
    known = str(f.value("name") or "")
    if f.crumbs:
        known += " | " + " > ".join(dict.fromkeys(str(c) for c in f.crumbs[:5]))
    m = re.search(r'(?:og|name=["\'])[:"\']description["\'][^>]*content=["\']([^"\']{20,300})',
                  html, re.I)
    if m:
        known += " | " + mine._unesc(m.group(1))
    return known


async def _category(guess, known: str, html: str) -> tuple[str | None, dict]:
    # descend the taxonomy: the guess names the branch, one pick inside it
    top = taxonomy.top(guess)
    if not top:
        return None, {}
    leaf, usage = await pick_leaf(known, html, taxonomy.subtree(top))
    return taxonomy.snap(leaf) or taxonomy.snap(guess), usage


async def extract(html: str) -> Product:
    t0 = time.time()
    scr = rungs.scripts(html)
    hint = _hint(html)
    f = _Fields()

    # rungs one and two are free parses, always run both
    f.merge(mine.jsonld(rungs.declared(scr)), "declared")
    f.merge(mine.state(rungs.shipped(scr), hint), "shipped")

    # rung three actually executes the page, only boot it when still short
    if f.core_missing():
        f.merge(mine.state(await asyncio.to_thread(rungs.computed, scr), hint), "computed")

    if "name" not in f.data and hint:
        f.merge({"name": hint.split("|")[0].strip()}, "declared")

    # a price the rendered page never shows is suspect
    if "price" in f.data:
        p, vis = float(f.value("price")), _visible_prices(html)
        if vis and not any(abs(p - v) <= 0.011 * max(p, v) for v in vis):
            f.disputes.add("price")

    # one model call for everything missing or disputed, category rides along
    missing = f.core_missing() + sorted(f.disputes)
    if "compare_at" not in f.data and "compare_at" not in missing and _SALE.search(html):
        missing.append("compare_at")
    known = _context(f, html)
    fb, usage = await infer(html, missing, taxonomy.TOPS, known)
    for k in missing:
        v = fb.get(k)
        if k in ("price", "compare_at") and v is not None:
            v = mine._num(v)
        if v is not None:
            f.set(k, v, "inferred")
        elif k not in f.data:
            f.set(k, None, "none")

    cat, usage2 = await _category(fb.get("category"), known, html)
    f.set("category", cat, "inferred" if cat else "none")
    usage = {k: (usage.get(k) or 0) + (usage2.get(k) or 0) for k in usage | usage2
             if isinstance(usage.get(k, usage2.get(k)), (int, float))}

    for k in ("name", "price", "compare_at", "currency", "category"):
        f.data.setdefault(k, Field(value=None, source="none"))

    # a compare at equal to the price just means not on sale
    p, ca = f.value("price"), f.value("compare_at")
    if p is not None and ca is not None and abs(float(ca) - float(p)) < 0.01:
        f.set("compare_at", None, f.data["compare_at"].source)
    cur = f.value("currency")
    if isinstance(cur, str):
        f.data["currency"].value = _SYM.get(cur.strip(), cur.strip().upper())

    return Product(
        **f.data,
        images=[str(u) for u in dict.fromkeys(f.images) if str(u).startswith("http")][:10],
        meta={
            "latency_s": round(time.time() - t0, 2),
            "llm_fields": missing + ["category"],
            "llm_tokens": usage,
            "sources": {k: fl.source for k, fl in f.data.items()},
        },
    )
