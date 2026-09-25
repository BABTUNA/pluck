"""
the router that turns one page into one product
runs the free parses on every page and only boots the sandbox or the model when needed
  extract   walk the rungs then referee disputes then let the model fill the rest
  _Fields   hold the answers while climbing and turn disagreements into disputes
"""

import asyncio
import re
import time

from pydantic import BaseModel

from . import mine, rungs, taxonomy
from .infer import infer, list_details, pick_leaf

_CORE = ("name", "price", "currency")
_TOL = 0.01  # prices within one percent count as the same number
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
        self.crumbs, self.images, self.variants = [], [], []
        self.options, self.colors = [], []
        self.video = self.description = None
        self.disputes: set[str] = set()

    # fold one rungs findings in without overwriting earlier rungs
    def merge(self, found: dict, source: str):
        for k, v in found.items():
            if k in ("crumbs", "images"):
                getattr(self, k).extend(v)
            elif k == "variants":
                # richer rung wins, shopify matrices beat sparse offer lists
                if len(v) > len(self.variants):
                    self.variants = v
            elif k in ("options", "colors", "video", "description"):
                setattr(self, k, getattr(self, k) or v)
            elif k == "conflict":
                # the page itself declared several prices so that is a dispute
                self.disputes.add("price")
            elif k not in self.data and v is not None:
                self.data[k] = Field(value=v, source=source)
            elif k in self.data and isinstance(v, (int, float)) \
                    and isinstance(self.data[k].value, (int, float)) \
                    and abs(v - self.data[k].value) > _TOL * max(v, self.data[k].value):
                # two rungs disagree by over a percent so the model referees
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


# token usage dicts from parallel calls fold into one
def _add_usage(a: dict, b: dict) -> dict:
    return {k: (a.get(k) or 0) + (b.get(k) or 0) for k in a | b
            if isinstance(a.get(k, b.get(k)), (int, float))}


# grab the page title to pick the right product out of state blobs
def _hint(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]{3,150})", html, re.I) \
        or re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']{3,150})', html, re.I)
    return mine.unesc(m.group(1)) if m else ""


_VIS = re.compile(r"[$€£¥]\s?(\d[\d.,]*)|(\d[\d.,]*)\s?(?:[$€£¥]|USD|EUR|GBP|kr\b)")


# grab every price a human can actually see on the rendered page
def _visible_prices(html: str) -> set[float]:
    text = re.sub(r"<(script|style|svg|noscript)[\s\S]*?</\1>|<[^>]+>", " ", html)
    vals = (mine.num(m.group(1) or m.group(2)) for m in _VIS.finditer(text))
    return {v for v in vals if v is not None and 0.5 <= v <= 500_000}


# build the identity string the model sees from name breadcrumbs and description
def _context(f: _Fields, html: str) -> str:
    known = str(f.value("name") or "")
    if f.crumbs:
        known += " | " + " > ".join(dict.fromkeys(str(c) for c in f.crumbs[:5]))
    m = re.search(r'(?:og:|name=["\'])description["\'][^>]*content=["\']([^"\']{20,300})',
                  html, re.I)
    if m:
        known += " | " + mine.unesc(m.group(1))
    return known


# fill the gallery from markup when the rungs found little: preload hero
# shots, then og image, then img tags at the largest srcset rendition
def _image_fallbacks(f: _Fields, html: str):
    if not f.images:
        for tag in re.findall(r"<link[^>]+>", html, re.I):
            if re.search(r'rel=["\']preload["\']', tag) and re.search(r'as=["\']image["\']', tag) \
                    and (m := re.search(r'href=["\'](//[^"\']+|https?://[^"\']+)', tag)):
                u = m.group(1)
                f.images.append("https:" + u if u.startswith("//") else u)
    if not f.images:
        f.images += re.findall(
            r'property=["\'](?:og|twitter):image["\'][^>]*content=["\'](http[^"\']+)', html, re.I)[:4]
    if len(f.images) >= 2:
        return
    seed_dir = f.images[0].rsplit("/", 1)[0] if f.images else None
    for tag in re.findall(r"<img[^>]+>", html, re.I):
        m = re.search(r'srcset=["\']([^"\']+)', tag) \
            or re.search(r'src=["\'](//[^"\']+|https?://[^"\']+)', tag)
        u = m.group(1).split(",")[-1].strip().split(" ")[0] if m else None
        if u and u.startswith("//"):
            u = "https:" + u
        if u and u.startswith("http") \
                and not re.search(r"logo|icon|sprite|pixel|badge|\.svg|\.gif", u, re.I) \
                and (seed_dir is None or u.startswith(seed_dir)):
            f.images.append(u)
        if len(f.images) >= 16:
            return


# the values along one named axis, colors fall out of the variant matrix free
def _axis_values(options: list, variants: list, axis: str) -> list[str]:
    for i, label in enumerate(options):
        if axis in str(label).lower():
            return list(dict.fromkeys(
                v["name"].split(" / ")[i] for v in variants
                if isinstance(v.get("name"), str) and len(v["name"].split(" / ")) > i))[:20]
    return []


# subtree videos are trusted, a raw page scan only counts when the page
# holds exactly one video so another colorways reel can never win
def _video(html: str, mined: str | None) -> str | None:
    if mined:
        return mined
    if m := re.search(r'property=["\']og:video[^"\']*["\'][^>]*content=["\'](http[^"\']+)', html, re.I):
        return m.group(1)
    found = {u.replace("\\/", "/") for u in re.findall(
        r'(?:https?:)?(?:\\/\\/|//)(?:[^"\'\s\\]|\\/)+\.(?:mp4|m3u8|webm)\b(?:[^"\'\s\\]|\\/)*', html)}
    if len(found) != 1:
        return None
    u = found.pop()
    return "https:" + u if u.startswith("//") else u


_REND = re.compile(r"[-_](full|max|standard|square|thumb|mini|small|medium|large"
                   r"|zoom|\d+x\d+)(?=\.|$)", re.I)
_RANKS = [re.compile(r"[-_](max|original|master)\b", re.I),
          re.compile(r"[-_](full|large|zoom|2048)\b", re.I), None,
          re.compile(r"[-_](thumb|mini|small|square|standard)\b", re.I)]


# one shot often ships as several renditions, keep only the largest of each
def _dedupe_renditions(images: list[str]) -> list[str]:
    rank = lambda u: next((i for i, p in enumerate(_RANKS) if p and p.search(u)), 2)
    best, order = {}, []
    for u in images:
        parts = u.rsplit("/", 2)
        key = (parts[-2] if len(parts) > 2 else "",
               _REND.sub("", parts[-1]).rsplit(".", 1)[0])
        if key not in best:
            order.append(key)
            best[key] = u
        elif rank(u) < rank(best[key]):
            best[key] = u
    return [best[k] for k in order]


# galleries share a filename prefix on most cdns, so when the first image's
# token has three or more siblings keep only that cluster
def _cluster(images: list[str]) -> list[str]:
    if len(images) < 4:
        return images
    tok = lambda u: re.split(r"[_-]", u.rsplit("/", 1)[-1])[0]
    same = [u for u in images if tok(u) == tok(images[0])]
    return same if len(same) >= 3 else images


# descend the taxonomy where the guess names the branch and one pick lands inside it
# only real paths are offered so the answer cannot be invented
async def _category(guess, known: str, html: str) -> tuple[str | None, dict]:
    top = taxonomy.top(guess)
    if not top:
        return None, {}
    fb, usage = await pick_leaf(known, html, taxonomy.subtree(top))
    return taxonomy.snap(fb.get("category")) or taxonomy.snap(guess), usage


# run the whole decision tree for one page
# free rungs first then the sandbox then the model for whatever is left
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
    if "brand" not in f.data and (m := re.search(
            r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']{2,60})', html, re.I)):
        f.merge({"brand": mine.unesc(m.group(1))}, "declared")
    _image_fallbacks(f, html)

    # a price the rendered page never shows is suspect
    if "price" in f.data:
        p, vis = float(f.value("price")), _visible_prices(html)
        if vis and not any(abs(p - v) <= _TOL * max(p, v) for v in vis):
            f.disputes.add("price")

    # one model call for everything missing or disputed and category rides along
    missing = list(dict.fromkeys(f.core_missing() + sorted(f.disputes)))
    if "compare_at" not in f.data and "compare_at" not in missing and _SALE.search(html):
        missing.append("compare_at")
    known = _context(f, html)
    fb, usage = await infer(html, missing, taxonomy.TOPS, known)
    for k in missing:
        v = fb.get(k)
        if k in ("price", "compare_at") and v is not None:
            v = mine.num(v)
        if v is not None:
            f.set(k, v, "inferred")
        elif k not in f.data:
            f.set(k, None, "none")

    # the leaf pick and the details ask run side by side, separate prompts
    # because sharing one measurably hurt the category answer
    (cat, usage2), (details, usage3) = await asyncio.gather(
        _category(fb.get("category"), known, html), list_details(known, html))
    f.set("category", cat, "inferred" if cat else "none")
    usage = _add_usage(usage, _add_usage(usage2, usage3))
    # model listed extras only count when the page text actually shows them
    text = re.sub(r"<[^>]+>", " ", html).lower()
    grounded = lambda t: str(t).lower() not in ("black / s", "black / m") and all(
        re.search(rf"\b{re.escape(w.strip())}\b", text, re.I)
        for w in str(t).split(" / ") if w.strip())
    if not f.variants:
        f.variants = [{"name": str(v)[:80], "price": None, "compare_at": None}
                      for v in details.get("variants", [])[:30]
                      if str(v).strip() and grounded(v)]
    colors = f.colors[:20] \
        or _axis_values(f.options, f.variants, "color") \
        or [str(c)[:40] for c in details.get("colors", [])[:20] if grounded(c)]
    features = [str(k)[:120] for k in details.get("key_features", [])[:8]]

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
        video_url=_video(html, f.video),
        images=_cluster(_dedupe_renditions([u for u in dict.fromkeys(
            mine.canon(i) for i in f.images) if u]))[:12],
        meta={"latency_s": round(time.time() - t0, 2),
              "llm_fields": missing + ["category"], "llm_tokens": usage,
              "sources": {k: fl.source for k, fl in f.data.items()}},
    )
