"""
the router that turns one page into one product
climbs four sources cheapest first and stops once name price and currency are filled
  extract          walk the rungs then referee disputes then let the model fill the rest
  _Fields          hold the answers while climbing and turn disagreements into disputes
  _visible_prices  grab every price a human can actually see on the rendered page
  _category        pick the top level branch then one exact path inside it
"""

import asyncio
import re
import time

from pydantic import BaseModel

from . import mine, rungs, taxonomy
from .infer import infer, list_details, pick_leaf

_CORE = ("name", "price", "currency")
_SALE = re.compile(r"was \$|% off|you save|original price|compare at|-\d+%", re.I)
_SYM = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "kr": "SEK"}


# a value plus which rung answered it
class Field(BaseModel):
    value: str | float | None
    source: str  # declared | shipped | computed | inferred | none


# the finished extraction where every field says which rung answered it
class Product(BaseModel):
    name: Field
    price: Field
    compare_at: Field
    currency: Field
    category: Field
    brand: Field
    description: str | None
    options: list[str]    # the axis labels for variant names, like Color and Size
    variants: list[dict]  # discrete configurations, {name, price, compare_at, available}
    colors: list[str]
    key_features: list[str]
    video_url: str | None
    images: list[str]
    meta: dict


# hold the fields while climbing
# first rung to answer wins and numeric disagreements become disputes for the model
class _Fields:
    def __init__(self):
        self.data: dict[str, Field] = {}
        self.crumbs: list = []
        self.images: list = []
        self.variants: list = []
        self.options: list = []
        self.description: str | None = None
        self.disputes: set[str] = set()

    # fold one rungs findings in without overwriting earlier rungs
    def merge(self, found: dict, source: str):
        for k, v in found.items():
            if k == "crumbs":
                self.crumbs += v
            elif k == "images":
                self.images += v
            elif k == "variants":
                # richer rung wins, shopify matrices beat sparse offer lists
                if len(v) > len(self.variants):
                    self.variants = v
            elif k == "options":
                self.options = self.options or v
            elif k == "description":
                self.description = self.description or v
            elif k == "conflict":
                # the page itself declared several prices so that is a dispute
                self.disputes.add("price")
            elif k not in self.data and v is not None:
                self.data[k] = Field(value=v, source=source)
            elif k in self.data and _differ(v, self.data[k].value):
                # two rungs disagree so the model referees
                self.disputes.add(k)

    # overwrite a field with a model answer or a final guard
    def set(self, k: str, v, source: str):
        self.data[k] = Field(value=v, source=source)

    # current value or none
    def value(self, k: str):
        return self.data[k].value if k in self.data else None

    # check which of name price currency are still unanswered
    def core_missing(self) -> list[str]:
        return [k for k in _CORE if k not in self.data]


# check if two numbers are more than one percent apart
def _differ(a, b) -> bool:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(a - b) > 0.01 * max(a, b)


# grab the page title to pick the right product out of state blobs
def _hint(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]{3,150})", html, re.I) \
        or re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']{3,150})', html, re.I)
    return mine._unesc(m.group(1)) if m else ""


_VIS = re.compile(r"[$€£¥]\s?(\d[\d.,]*)|(\d[\d.,]*)\s?(?:[$€£¥]|USD|EUR|GBP|kr\b)")


# grab every price a human can actually see on the rendered page
def _visible_prices(html: str) -> set[float]:
    text = re.sub(r"<(script|style|svg|noscript)[\s\S]*?</\1>|<[^>]+>", " ", html)
    out = set()
    for m in _VIS.finditer(text):
        v = mine._num(m.group(1) or m.group(2))
        if v is not None and 0.5 <= v <= 500_000:
            out.add(v)
    return out


# build the identity string the model sees from name breadcrumbs and description
def _context(f: _Fields, html: str) -> str:
    known = str(f.value("name") or "")
    if f.crumbs:
        known += " | " + " > ".join(dict.fromkeys(str(c) for c in f.crumbs[:5]))
    m = re.search(r'(?:og|name=["\'])[:"\']description["\'][^>]*content=["\']([^"\']{20,300})',
                  html, re.I)
    if m:
        known += " | " + mine._unesc(m.group(1))
    return known


# the values along one named axis, colors fall out of the variant matrix free
def _axis_values(options: list, variants: list, axis: str) -> list[str]:
    for i, label in enumerate(options):
        if axis in str(label).lower():
            vals = [v["name"].split(" / ")[i] for v in variants
                    if isinstance(v.get("name"), str) and len(v["name"].split(" / ")) > i]
            return list(dict.fromkeys(vals))[:20]
    return []


# descend the taxonomy where the guess names the branch and one pick lands inside it
# only real paths are offered so the answer cannot be invented
async def _category(guess, known: str, html: str) -> tuple[str | None, dict]:
    top = taxonomy.top(guess)
    if not top:
        return None, {}
    fb, usage = await pick_leaf(known, html, taxonomy.subtree(top))
    return taxonomy.snap(fb.get("category")) or taxonomy.snap(guess), usage


# run the whole decision tree for one page
# free rungs first then the sandbox then one model call for whatever is left
async def extract(html: str) -> Product:
    t0 = time.time()
    scr = rungs.scripts(html)
    hint = _hint(html)
    f = _Fields()

    # rungs one and two are free parses so always run both
    f.merge(mine.jsonld(rungs.declared(scr)), "declared")
    f.merge(mine.state(rungs.shipped(scr), hint), "shipped")

    # rung three actually executes the page so only boot it when still short
    if f.core_missing():
        f.merge(mine.state(await asyncio.to_thread(rungs.computed, scr), hint), "computed")

    if "name" not in f.data and hint:
        f.merge({"name": hint.split("|")[0].strip()}, "declared")
    if "brand" not in f.data:
        m = re.search(r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']{2,60})', html, re.I)
        if m:
            f.merge({"brand": mine._unesc(m.group(1))}, "declared")

    # pages preload their hero product shots, a high precision signal
    if not f.images:
        for tag in re.findall(r"<link[^>]+>", html, re.I):
            if re.search(r'rel=["\']preload["\']', tag) and re.search(r'as=["\']image["\']', tag):
                m = re.search(r'href=["\'](//[^"\']+|https?://[^"\']+)', tag)
                if m:
                    u = m.group(1)
                    f.images.append("https:" + u if u.startswith("//") else u)
    # most stores declare a hero photo in og image even when json ld has none
    if not f.images:
        f.images += re.findall(
            r'property=["\'](?:og|twitter):image["\'][^>]*content=["\'](http[^"\']+)', html)[:4]
    # last resort for the gallery, the pages own img tags minus the chrome
    # srcset lists renditions smallest to largest so take the last, the
    # assignment wants full resolution not the lazy loader thumb
    if len(f.images) < 2:
        for tag in re.findall(r"<img[^>]+>", html, re.I):
            m = re.search(r'srcset=["\']([^"\']+)', tag)
            u = m.group(1).split(",")[-1].strip().split(" ")[0] if m else None
            if not u:
                m = re.search(r'src=["\'](//[^"\']+|https?://[^"\']+)', tag)
                u = m.group(1) if m else None
            if u and u.startswith("//"):
                u = "https:" + u
            if u and u.startswith("http")                     and not re.search(r"logo|icon|sprite|pixel|badge|\.svg|\.gif", u, re.I):
                f.images.append(u)
            if len(f.images) >= 8:
                break

    # a price the rendered page never shows is suspect
    if "price" in f.data:
        p, vis = float(f.value("price")), _visible_prices(html)
        if vis and not any(abs(p - v) <= 0.011 * max(p, v) for v in vis):
            f.disputes.add("price")

    # one model call for everything missing or disputed and category rides along
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

    # the leaf pick and the details ask run side by side, separate prompts
    # because sharing one measurably hurt the category answer
    (cat, usage2), (details, usage3) = await asyncio.gather(
        _category(fb.get("category"), known, html), list_details(known, html))
    f.set("category", cat, "inferred" if cat else "none")
    if not f.variants:
        f.variants = [{"name": str(v)[:80], "price": None, "compare_at": None}
                      for v in details.get("variants", [])[:30] if str(v).strip()]
    colors = _axis_values(f.options, f.variants, "color") \
        or [str(c)[:40] for c in details.get("colors", [])[:20]]
    features = [str(k)[:120] for k in details.get("key_features", [])[:8]]
    # product videos sit as plain cdn urls in the state, og video is rare
    video = re.search(r'property=["\']og:video[^"\']*["\'][^>]*content=["\'](http[^"\']+)', html, re.I) \
        or re.search(r'((?:https?:)?(?:\\/\\/|//)[^"\'\s\\]+\.(?:mp4|m3u8|webm)\b[^"\'\s\\]*)', html)
    usage2 = {k: (usage2.get(k) or 0) + (usage3.get(k) or 0) for k in usage2 | usage3
              if isinstance(usage2.get(k, usage3.get(k)), (int, float))}
    usage = {k: (usage.get(k) or 0) + (usage2.get(k) or 0) for k in usage | usage2
             if isinstance(usage.get(k, usage2.get(k)), (int, float))}

    for k in ("name", "price", "compare_at", "currency", "category", "brand"):
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
        description=f.description,
        options=f.options[:4],
        variants=f.variants[:30],
        colors=colors,
        key_features=features,
        video_url=("https:" + v if (v := video.group(1).replace("\\/", "/")).startswith("//")
                   else v) if video else None,
        images=[str(u).replace(":////", "://") for u in dict.fromkeys(f.images)
                if str(u).startswith("http")][:10],
        meta={
            "latency_s": round(time.time() - t0, 2),
            "llm_fields": missing + ["category"],
            "llm_tokens": usage,
            "sources": {k: fl.source for k, fl in f.data.items()},
        },
    )
