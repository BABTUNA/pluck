"""
one miner for every source since every rung produces json
  jsonld  extract the merchants declared answer, following hasVariant groups
  state   pick the subtree whose name matches the page title, mine descendants
  canon   normalize an image url, strip size params so full res dedupes clean
"""

import html as _html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_NAME_KEYS = set("title name product_title producttitle displayname productname "
                 "product_name fulltitle full_title".split())
_PRICE_KEYS = set("price current_price currentprice sale_price saleprice "
                  "price_amount priceamount".split())
_COMPARE_KEYS = set("compare_at_price compareatprice compare_at list_price listprice "
                    "original_price originalprice was_price wasprice regular_price "
                    "regularprice strikethroughprice initialprice initial_price msrp "
                    "fullprice full_price standardprice".split())
_CURRENCY_KEYS = set("currency currencycode currency_code pricecurrency".split())
_CRUMB_KEYS = set("category product_type producttype product_category".split())
_VARIANT_LIST_KEYS = set("variants items skus sizes".split())
# renditions ranked so a sources dict yields its largest copy
_BIG = re.compile(r"max|original|master|full|large|2048|1024|zoom", re.I)
_SMALL = re.compile(r"mini|thumb|small|icon|tiny|micro|swatch", re.I)
_JUNK_KEY = re.compile(r"related|recommend|styled|similar|upsell|crosssell|recently"
                       r"|breadcrumb|navigation|reviews")
_IMG_URL = re.compile(r"^(?:https?:)?//[^\s\"']+\.(?:jpe?g|png|webp|avif)(?:[?#]|$)", re.I)
# size and cache params stripped so renditions of one shot dedupe to full res
_STRIP_PARAM = re.compile(r"^(w|h|q|quality|fit|max|width|height|sw|sh|fm|crop|wid|hei"
                          r"|resmode|_mzcb|v|cb|impolicy|imwidth|imheight|scale|size)$", re.I)


# unescape twice since pages double encode entities
def unesc(s: str) -> str:
    return _html.unescape(_html.unescape(s)).strip()


def _key(k) -> str:
    return str(k).lower().replace("-", "_")


# normalize an image url and strip the size params
def canon(url) -> str | None:
    u = unesc(str(url)).replace("\\/", "/").strip()
    u = "https:" + u if u.startswith("//") else u
    if not u.startswith("http"):
        return None
    parts = urlsplit(u)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _STRIP_PARAM.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


# parse money in any shape into a float
def num(v) -> float | None:
    if isinstance(v, dict):
        v = v.get("amount") or v.get("value") or v.get("price")
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = re.sub(r"[^\d.,]", "", v)
    # handle 1.299,90 european style vs 1,299.90
    if s.count(",") == 1 and re.search(r",\d{2}$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# keep prices inside sane bounds
def _ok_price(v: float | None) -> bool:
    return v is not None and 0.5 <= v <= 500_000


def _toks(s) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


# extract the declared answer from schema org product blocks
# several distinct offer prices counts as ambiguity so the tree climbs
def jsonld(objs: list) -> dict:
    out: dict = {}
    stack = list(objs)
    while stack:
        o = stack.pop()
        if isinstance(o, list):
            stack.extend(o)
            continue
        if not isinstance(o, dict):
            continue
        # a productgroup declares its size and color matrix under hasvariant
        stack.extend((o.get("@graph") or []) + (o.get("hasVariant") or []))
        t = " ".join(o["@type"]) if isinstance(o.get("@type"), list) else (o.get("@type") or "")
        if "BreadcrumbList" in t:
            for el in o.get("itemListElement") or []:
                el = el if isinstance(el, dict) else {}
                nm = (el.get("item") or {}).get("name") if isinstance(el.get("item"), dict) \
                    else el.get("name")
                if isinstance(nm, str):
                    out.setdefault("crumbs", []).append(nm[:60])
        if "Product" not in t:
            continue
        if isinstance(o.get("name"), str):
            out.setdefault("name", unesc(o["name"]))
        b = o["brand"].get("name") if isinstance(o.get("brand"), dict) else o.get("brand")
        if isinstance(b, str) and b.strip():
            out.setdefault("brand", unesc(b)[:60])
        if isinstance(o.get("description"), str) and len(o["description"]) > 20:
            out.setdefault("description", unesc(o["description"])[:600])
        if isinstance(o.get("category"), str):
            out.setdefault("crumbs", []).append(o["category"])
        if isinstance(o.get("color"), str) and o["color"].strip():
            out.setdefault("colors", []).append(unesc(o["color"])[:60])
        img = o.get("image")
        for i in img if isinstance(img, list) else [img] if img else []:
            u = canon(i.get("url") if isinstance(i, dict) else i)
            if u and u not in out.setdefault("images", []):
                out["images"].append(u)
        # a variant product carries its own size and offer
        offers = o.get("offers") or {}
        for offer in offers if isinstance(offers, list) else [offers]:
            if not isinstance(offer, dict):
                continue
            p = num(offer.get("price") or offer.get("lowPrice")
                    or (offer.get("priceSpecification") or {}))
            if not _ok_price(p):
                continue
            out.setdefault("prices", set()).add(round(p, 2))
            if offer.get("priceCurrency") and not out.get("currency"):
                out["currency"] = offer["priceCurrency"]
            vname = offer.get("name") or o.get("size")
            if isinstance(vname, str) and vname.strip():
                out.setdefault("variants", []).append(
                    {"name": unesc(vname)[:80], "price": p, "compare_at": None})
    if out.get("colors"):
        out["colors"] = list(dict.fromkeys(out["colors"]))[:20]
    # sized variants all at one price is a matrix not an ambiguity
    prices = out.pop("prices", set())
    if len(prices) == 1:
        out["price"] = prices.pop()
    elif len(prices) > 1:
        out["conflict"] = True
    out["images"] = (out.get("images") or [])[:16]
    return {k: v for k, v in out.items() if v}


# extract the main product from framework state
# the hint is the page title, the best matching subtree wins and everything
# under it gets mined so price and images need not share one dict
def state(objs: list, hint: str = "") -> dict:
    hint_toks = _toks(hint)
    best, best_score, cur_seen, crumbs = None, 0, None, []

    def score_walk(o, depth):
        nonlocal best, best_score, cur_seen
        if depth > 14:
            return
        if isinstance(o, list):
            for v in o[:120]:
                score_walk(v, depth + 1)
            return
        if not isinstance(o, dict):
            return
        for k, v in o.items():
            lk = _key(k)
            if cur_seen is None and lk in _CURRENCY_KEYS:
                cur_seen = _currency(v)
            # category style strings ride along, numeric ids are not categories
            if lk in _CRUMB_KEYS and isinstance(v, str) and len(v) > 2 \
                    and re.search(r"[a-zA-Z]", v) and v not in crumbs:
                crumbs.append(v[:60])
        if name := _name_of(o):
            overlap = len(_toks(name) & hint_toks)
            has_matrix = any(k.lower() in _VARIANT_LIST_KEYS and isinstance(v, list)
                             for k, v in o.items())
            has_price = any(_key(k) in _PRICE_KEYS or k.lower() == "prices" for k in o)
            s = 2 * overlap + 2 * has_matrix + has_price
            if s > best_score and (overlap or has_matrix or has_price):
                best, best_score = o, s
        for v in o.values():
            score_walk(v, depth + 1)

    score_walk(objs, 0)
    out = _mine_subtree(best) if best is not None else {}
    if cur_seen and not out.get("currency"):
        out["currency"] = cur_seen
    if crumbs and not out.get("crumbs"):
        out["crumbs"] = crumbs[:6]
    return out


# the products name may be a direct key, inside a child info dict, or on
# the first variant when the parent carries no title of its own
def _name_of(o: dict) -> str | None:
    def direct(d):
        return next((v for k, v in d.items() if _key(k) in _NAME_KEYS
                     and isinstance(v, str) and 3 <= len(v) <= 150), None)
    if n := direct(o):
        return n
    if n := next((n for v in o.values() if isinstance(v, dict) and (n := direct(v))), None):
        return n
    for k, v in o.items():
        if k.lower() in _VARIANT_LIST_KEYS and isinstance(v, list) and v \
                and isinstance(v[0], dict):
            nm = v[0].get("name") or v[0].get("title")
            if isinstance(nm, str) and 3 <= len(nm) <= 150:
                return nm
    return None


def _currency(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("active") or v.get("code") or v.get("isoCode") or ""
    return v if isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v) else None


# mine every field from the winning product subtree, shallowest answer first
def _mine_subtree(root: dict) -> dict:
    out: dict = {}
    images, colors = [], []
    queue = [(root, "handle" in root or "compare_at_price" in root, 0)]
    seen = 0
    while queue and seen < 4000:
        node, shopify, depth = queue.pop(0)
        seen += 1
        if depth > 10:
            continue
        if isinstance(node, list):
            queue += [(v, shopify, depth + 1) for v in node[:120]
                      if isinstance(v, (dict, list))]
            continue
        shopify = shopify or "handle" in node or "compare_at_price" in node
        for k, v in node.items():
            lk = _key(k)
            if lk in _NAME_KEYS and isinstance(v, str) and 3 <= len(v) <= 150:
                out.setdefault("name", unesc(v))
            elif lk in _PRICE_KEYS or lk in _COMPARE_KEYS:
                field = "price" if lk in _PRICE_KEYS else "compare_at"
                p = _cents(num(v), v, shopify)
                if _ok_price(p):
                    out.setdefault(field, p)
            elif lk in _CURRENCY_KEYS and not out.get("currency"):
                if c := _currency(v):
                    out["currency"] = c
            elif lk == "options" and isinstance(v, list) and v and "options" not in out:
                labels = [x.get("name") if isinstance(x, dict) else x for x in v[:4]]
                if labels := [str(x) for x in labels if isinstance(x, str) and x.strip()]:
                    out["options"] = labels
            elif lk in _VARIANT_LIST_KEYS and isinstance(v, list) and v \
                    and "variants" not in out:
                vs = [x for x in (_variant(i) for i in v[:40]) if x]
                if len(vs) >= max(2, len(v[:40]) // 2):
                    out["variants"] = vs[:30]
                    # shopify keeps the real prices on the variants
                    if (shopify or lk == "variants") and isinstance(v[0], dict):
                        for key, field in (("price", "price"),
                                           ("compare_at_price", "compare_at")):
                            p = _cents(num(v[0].get(key)), v[0].get(key), True)
                            if _ok_price(p):
                                out[field] = p
            elif ("color" in lk or "swatch" in lk) and isinstance(v, dict):
                # a name next to a hex code is a color swatch on any platform
                nm, hx = v.get("name") or v.get("label"), v.get("color") or v.get("hex")
                if isinstance(nm, str) and isinstance(hx, str) and hx.startswith("#") \
                        and nm not in colors:
                    colors.append(unesc(nm)[:40])
            if re.search(r"image|media|gallery|photo", lk):
                _collect_images(v, images, trusted=True)
            elif isinstance(v, str):
                _collect_images(v, images)
            if "video" in lk and "video" not in out:
                if u := _video_url(v):
                    out["video"] = u
            if isinstance(v, (dict, list)) and not _JUNK_KEY.search(lk):
                queue.append((v, shopify, depth + 1))
    if images:
        out["images"] = list(dict.fromkeys(images))[:16]
    if colors:
        out["colors"] = colors[:20]
    if "variants" in out and "name" not in out:
        # some product dicts carry no title, the variants hold the full name
        full = out["variants"][0].get("name")
        if isinstance(full, str) and len(full) >= 3:
            out["name"] = full[:150]
    return {k: v for k, v in out.items() if v is not None and v != []}


# pull image urls out of a value, ranking rendition dicts largest first
# trusted means we arrived through an image keyed path, so scene7 and mozu
# style urls without file extensions still count
def _collect_images(v, images: list[str], depth: int = 0, trusted: bool = False):
    if len(images) >= 16 or depth > 3:
        return
    if isinstance(v, str):
        v = v.strip()
        looks = _IMG_URL.match(v) or (trusted and re.match(r"^(?:https?:)?//\S+/\S+", v)
                                      and not re.search(r"\.(js|css|json|html?)\b", v))
        if looks and (u := canon(v)):
            images.append(u)
    elif isinstance(v, dict):
        # a dict of rendition names holds copies of one shot, take the biggest
        ranked = sorted(v, key=lambda k: 0 if _BIG.search(k) else
                        2 if _SMALL.search(k) else 1)
        if ranked and any(_BIG.search(k) or _SMALL.search(k) for k in v) \
                and all(isinstance(v[k], (str, list, dict)) for k in v):
            return _collect_images(v[ranked[0]], images, depth + 1, True)
        for k in ("url", "src", "href"):
            if isinstance(v.get(k), str):
                return _collect_images(v[k], images, depth + 1, True)
        for k in v:
            if isinstance(v[k], (dict, list)):
                _collect_images(v[k], images, depth + 1, trusted)
    elif isinstance(v, list):
        for it in v[:30]:
            _collect_images(it, images, depth + 1, trusted)


# a video url hides under video keys as a string or nested file dict
def _video_url(v, depth: int = 0):
    if isinstance(v, str) and re.search(r"\.(mp4|m3u8|webm)\b", v):
        u = unesc(v).replace("\\/", "/")
        return "https:" + u if u.startswith("//") else (u if u.startswith("http") else None)
    if isinstance(v, dict) and depth < 3:
        for k, vv in v.items():
            if k.lower() in ("url", "src", "file", "videourl") and (u := _video_url(vv, depth + 1)):
                return u
    return None


# one discrete configuration of the product, like a size or color
# sku bearing members are the tell that a generic items list is variants
def _variant(vr) -> dict | None:
    if not isinstance(vr, dict):
        return None
    name = vr.get("title") or vr.get("public_title") or vr.get("name") \
        or vr.get("label") or " / ".join(
            str(vr[k]) for k in ("option1", "option2", "option3") if vr.get(k))
    if not isinstance(name, str) or not name.strip() or len(name) > 120 \
            or not any(k.lower() in ("sku", "ean", "merchskuid", "gtin", "gtins",
                                     "sizeid", "option1", "public_title",
                                     "compare_at_price", "price") for k in vr):
        return None
    price = _cents(num(vr.get("price")), vr.get("price"), True)
    comp = _cents(num(vr.get("compare_at_price")), vr.get("compare_at_price"), True)
    v = {"name": unesc(name)[:80], "price": price if _ok_price(price) else None,
         "compare_at": comp if _ok_price(comp) else None}
    if vr.get("available") is not None:
        v["available"] = bool(vr["available"])
    elif isinstance(vr.get("stock"), int):
        v["available"] = vr["stock"] > 0
    elif isinstance(vr.get("status"), str):
        v["available"] = vr["status"].upper() in ("ACTIVE", "OK", "IN_STOCK", "INSTOCK")
    return v


# an integer 8940 in a shopify blob means 89.40
def _cents(n: float | None, raw, shopify: bool) -> float | None:
    if n is not None and shopify and isinstance(raw, int) and n >= 100:
        return n / 100
    return n
