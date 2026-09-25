"""
one miner for every source since every rung produces json
finds the product subtree the page is about then mines everything beneath it
  jsonld  extract the merchants declared answer, following hasVariant groups
  state   pick the subtree whose name matches the page title, mine descendants
  canon   normalize an image url, strip size params so full res dedupes clean
  num     parse money in any shape like 129.9 or "$129.90" or cents ints or amount dicts
"""

import html as _html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_NAME_KEYS = {"title", "name", "product_title", "producttitle", "displayname",
              "productname", "product_name", "fulltitle", "full_title"}
_PRICE_KEYS = {"price", "current_price", "currentprice", "sale_price", "saleprice",
               "price_amount", "priceamount"}
_COMPARE_KEYS = {"compare_at_price", "compareatprice", "compare_at", "list_price",
                 "listprice", "original_price", "originalprice", "was_price",
                 "wasprice", "regular_price", "regularprice", "strikethroughprice",
                 "initialprice", "initial_price", "msrp", "fullprice", "full_price",
                 "standardprice"}
_CURRENCY_KEYS = {"currency", "currencycode", "currency_code", "pricecurrency"}
_CRUMB_KEYS = {"category", "product_type", "producttype", "product_category"}
_VARIANT_LIST_KEYS = {"variants", "items", "skus", "sizes"}
# renditions ranked so a sources dict yields its largest copy
_BIG = re.compile(r"max|original|master|full|large|2048|1024|zoom", re.I)
_SMALL = re.compile(r"mini|thumb|small|icon|tiny|micro|swatch", re.I)
_JUNK_KEY = re.compile(r"related|recommend|styled|similar|upsell|crosssell|recently"
                       r"|breadcrumb|navigation|reviews")
_IMG_URL = re.compile(r"^(?:https?:)?//[^\s\"']+\.(?:jpe?g|png|webp|avif)(?:[?#]|$)", re.I)
# size and cache params stripped so renditions of one shot dedupe to full res
_STRIP_PARAM = re.compile(
    r"^(w|h|q|quality|fit|max|width|height|sw|sh|fm|crop|wid|hei|resmode|_mzcb|v|cb"
    r"|impolicy|imwidth|imheight|scale|size)$", re.I)


# unescape twice since pages double encode entities
def unesc(s: str) -> str:
    return _html.unescape(_html.unescape(s)).strip()


# normalize an image url and strip the size params
def canon(url) -> str | None:
    u = unesc(str(url)).replace("\\/", "/").strip()
    if u.startswith("//"):
        u = "https:" + u
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
    if isinstance(v, str):
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
        stack.extend(o.get("@graph") or [])
        # a productgroup declares its size and color matrix under hasvariant
        variants = o.get("hasVariant") or []
        stack.extend(variants)
        t = o.get("@type") or ""
        t = " ".join(t) if isinstance(t, list) else t
        if "BreadcrumbList" in t:
            for el in o.get("itemListElement") or []:
                item = el.get("item") if isinstance(el, dict) else None
                nm = (item.get("name") if isinstance(item, dict) else None) \
                    or (el.get("name") if isinstance(el, dict) else None)
                if isinstance(nm, str):
                    out.setdefault("crumbs", []).append(nm[:60])
        if "Product" not in t:
            continue
        if isinstance(o.get("name"), str):
            out.setdefault("name", unesc(o["name"]))
        b = o.get("brand")
        b = b.get("name") if isinstance(b, dict) else b
        if isinstance(b, str) and b.strip():
            out.setdefault("brand", unesc(b)[:60])
        if isinstance(o.get("description"), str) and len(o["description"]) > 20:
            out.setdefault("description", unesc(o["description"])[:600])
        if isinstance(o.get("category"), str):
            out.setdefault("crumbs", []).append(o["category"])
        if isinstance(o.get("color"), str) and o["color"].strip():
            out.setdefault("colors", []).append(unesc(o["color"])[:60])
        img = o.get("image")
        imgs = img if isinstance(img, list) else [img] if img else []
        for i in imgs:
            u = canon(i.get("url") if isinstance(i, dict) else i)
            if u and u not in out.setdefault("images", []):
                out["images"].append(u)
        # a variant product carries its own size and offer
        size = o.get("size")
        offers = o.get("offers") or {}
        for offer in offers if isinstance(offers, list) else [offers]:
            if not isinstance(offer, dict):
                continue
            p = num(offer.get("price") or offer.get("lowPrice")
                    or (offer.get("priceSpecification") or {}))
            if _ok_price(p):
                out.setdefault("prices", set()).add(round(p, 2))
                if offer.get("priceCurrency") and not out.get("currency"):
                    out["currency"] = offer["priceCurrency"]
                vname = offer.get("name") if isinstance(offer.get("name"), str) else \
                    (size if isinstance(size, str) else None)
                if vname and vname.strip():
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
    return {k: v for k, v in out.items() if v or v == []}


# extract the main product from framework state
# the hint is the page title, the best matching subtree wins and everything
# under it gets mined so price and images need not share one dict
def state(objs: list, hint: str = "") -> dict:
    hint_toks = _toks(hint)
    best, best_score = None, 0
    cur_seen = None

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
            lk = k.lower().replace("-", "_")
            if cur_seen is None and lk in _CURRENCY_KEYS:
                cur_seen = _currency(v)
        name = _name_of(o)
        if name:
            overlap = len(_toks(name) & hint_toks)
            has_matrix = any(k.lower() in _VARIANT_LIST_KEYS and isinstance(v, list)
                             for k, v in o.items())
            has_price = any(k.lower().replace("-", "_") in _PRICE_KEYS
                            or k.lower() == "prices" for k in o)
            s = 2 * overlap + 2 * has_matrix + has_price
            if s > best_score and (overlap or has_matrix or has_price):
                best, best_score = o, s
        for v in o.values():
            score_walk(v, depth + 1)

    score_walk(objs, 0)
    out = _mine_subtree(best) if best is not None else {}
    if cur_seen and not out.get("currency"):
        out["currency"] = cur_seen
    crumbs = _crumbs(objs)
    if crumbs and not out.get("crumbs"):
        out["crumbs"] = crumbs
    return out


# the products name may be a direct key, inside a child info dict, or on
# the first variant when the parent carries no title of its own
def _name_of(o: dict) -> str | None:
    for k, v in o.items():
        if k.lower().replace("-", "_") in _NAME_KEYS and isinstance(v, str) \
                and 3 <= len(v) <= 150:
            return v
    for v in o.values():
        if isinstance(v, dict):
            for kk, vv in v.items():
                if kk.lower().replace("-", "_") in _NAME_KEYS and isinstance(vv, str) \
                        and 3 <= len(vv) <= 150:
                    return vv
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
    images: list[str] = []
    colors: list[str] = []
    shopify_root = "handle" in root or "compare_at_price" in root
    queue: list[tuple[dict | list, bool, int]] = [(root, shopify_root, 0)]
    seen_nodes = 0
    while queue and seen_nodes < 4000:
        node, shopify, depth = queue.pop(0)
        seen_nodes += 1
        if depth > 10:
            continue
        if isinstance(node, list):
            for v in node[:120]:
                if isinstance(v, (dict, list)):
                    queue.append((v, shopify, depth + 1))
            continue
        shopify = shopify or "handle" in node or "compare_at_price" in node
        for k, v in node.items():
            lk = k.lower().replace("-", "_")
            if lk in _NAME_KEYS and isinstance(v, str) and 3 <= len(v) <= 150:
                out.setdefault("name", unesc(v))
            elif lk in _PRICE_KEYS:
                p = _cents(num(v), v, shopify)
                if _ok_price(p):
                    out.setdefault("price", p)
            elif lk in _COMPARE_KEYS:
                p = _cents(num(v), v, shopify)
                if _ok_price(p):
                    out.setdefault("compare_at", p)
            elif lk in _CURRENCY_KEYS and not out.get("currency"):
                if c := _currency(v):
                    out["currency"] = c
            elif lk == "options" and isinstance(v, list) and v and "options" not in out:
                labels = [x.get("name") if isinstance(x, dict) else x for x in v[:4]]
                labels = [str(x) for x in labels if isinstance(x, str) and x.strip()]
                if labels:
                    out["options"] = labels
            elif lk in _VARIANT_LIST_KEYS and isinstance(v, list) and v \
                    and "variants" not in out:
                vs = [x for x in (_variant(i) for i in v[:40]) if x]
                if len(vs) >= max(2, len(v[:40]) // 2):
                    out["variants"] = vs[:30]
                    # shopify keeps the real prices on the variants
                    if shopify or lk == "variants":
                        vr = v[0] if isinstance(v[0], dict) else {}
                        for key, field in (("price", "price"),
                                           ("compare_at_price", "compare_at")):
                            p = _cents(num(vr.get(key)), vr.get(key), True)
                            if _ok_price(p):
                                out[field] = p
            elif ("color" in lk or "swatch" in lk) and isinstance(v, dict):
                # a name next to a hex code is a color swatch on any platform
                nm, hx = v.get("name") or v.get("label"), v.get("color") or v.get("hex")
                if isinstance(nm, str) and isinstance(hx, str) and hx.startswith("#"):
                    if nm not in colors:
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
        keys = list(v)
        # a dict of rendition names holds copies of one shot, take the biggest
        ranked = sorted(keys, key=lambda k: (0 if _BIG.search(k) else
                                             2 if _SMALL.search(k) else 1))
        if ranked and all(isinstance(v[k], (str, list, dict)) for k in ranked) \
                and any(_BIG.search(k) or _SMALL.search(k) for k in keys):
            _collect_images(v[ranked[0]], images, depth + 1, True)
            return
        for k in ("url", "src", "href"):
            if isinstance(v.get(k), str):
                _collect_images(v[k], images, depth + 1, True)
                return
        for k in keys:
            if isinstance(v[k], (dict, list)):
                _collect_images(v[k], images, depth + 1, trusted)
    elif isinstance(v, list):
        for it in v[:30]:
            _collect_images(it, images, depth + 1, trusted)


# a video url hides under video keys as a string or nested file dict
def _video_url(v, depth: int = 0):
    if depth > 3:
        return None
    if isinstance(v, str) and re.search(r"\.(mp4|m3u8|webm)\b", v):
        u = unesc(v).replace("\\/", "/")
        return "https:" + u if u.startswith("//") else (u if u.startswith("http") else None)
    if isinstance(v, dict):
        for k in ("url", "src", "file", "videourl", "videoURL"):
            for kk, vv in v.items():
                if kk.lower() == k.lower():
                    if u := _video_url(vv, depth + 1):
                        return u
    return None


# one discrete configuration of the product, like a size or color
def _variant(vr) -> dict | None:
    if not isinstance(vr, dict):
        return None
    name = vr.get("title") or vr.get("public_title") or vr.get("name") \
        or vr.get("label") or " / ".join(
            str(vr[k]) for k in ("option1", "option2", "option3") if vr.get(k))
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        return None
    # sku bearing members are the tell that a generic items list is variants
    if not any(k.lower() in ("sku", "ean", "merchskuid", "gtin", "gtins", "sizeid",
                             "option1", "public_title", "compare_at_price", "price")
               for k in vr):
        return None
    v = {"name": unesc(name)[:80],
         "price": _cents(num(vr.get("price")), vr.get("price"), True),
         "compare_at": _cents(num(vr.get("compare_at_price")),
                              vr.get("compare_at_price"), True)}
    if not _ok_price(v["price"]):
        v["price"] = None
    if not _ok_price(v["compare_at"]):
        v["compare_at"] = None
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


# collect category style strings anywhere in the state, ids are not categories
def _crumbs(objs, depth: int = 0) -> list[str]:
    found: list[str] = []
    if depth > 10:
        return found
    if isinstance(objs, list):
        for v in objs[:60]:
            found += _crumbs(v, depth + 1)
    elif isinstance(objs, dict):
        for k, v in objs.items():
            if k.lower().replace("-", "_") in _CRUMB_KEYS and isinstance(v, str) \
                    and len(v) > 2 and re.search(r"[a-zA-Z]", v):
                found.append(v[:60])
            else:
                found += _crumbs(v, depth + 1)
    return list(dict.fromkeys(found))[:6]
