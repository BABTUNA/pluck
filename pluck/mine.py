"""
one miner for every source since every rung produces json
walks any json for product fields so the rungs stay dumb harvesters
  jsonld  extract the merchants declared answer from schema org blocks
  state   extract the main product from framework state blobs
  _num    parse money in any shape like 129.9 or "$129.90" or cents ints or amount dicts
"""

import html as _html
import re

_NAME_KEYS = {"title", "name", "product_title", "producttitle", "displayname"}
_PRICE_KEYS = {"price", "current_price", "currentprice", "sale_price", "saleprice",
               "price_amount", "priceamount"}
_COMPARE_KEYS = {"compare_at_price", "compareatprice", "compare_at", "list_price",
                 "listprice", "original_price", "originalprice", "was_price",
                 "wasprice", "regular_price", "regularprice", "strikethroughprice"}
_CURRENCY_KEYS = {"currency", "currencycode", "currency_code", "pricecurrency"}
_CRUMB_KEYS = {"category", "product_type", "producttype", "product_category"}


# unescape twice since pages double encode entities
def _unesc(s: str) -> str:
    return _html.unescape(_html.unescape(s)).strip()


# parse money in any shape into a float
def _num(v) -> float | None:
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
            out.setdefault("name", _unesc(o["name"]))
        b = o.get("brand")
        b = b.get("name") if isinstance(b, dict) else b
        if isinstance(b, str) and b.strip():
            out.setdefault("brand", _unesc(b)[:60])
        if isinstance(o.get("description"), str) and len(o["description"]) > 20:
            out.setdefault("description", _unesc(o["description"])[:600])
        if isinstance(o.get("category"), str):
            out.setdefault("crumbs", []).append(o["category"])
        img = o.get("image")
        imgs = img if isinstance(img, list) else [img] if img else []
        out.setdefault("images", [i.get("url") if isinstance(i, dict) else i
                                  for i in imgs if i])
        offers = o.get("offers") or {}
        for offer in offers if isinstance(offers, list) else [offers]:
            if not isinstance(offer, dict):
                continue
            p = _num(offer.get("price") or offer.get("lowPrice")
                     or (offer.get("priceSpecification") or {}))
            if _ok_price(p):
                out.setdefault("prices", set()).add(round(p, 2))
                out.setdefault("currency", offer.get("priceCurrency"))
                # named offers are the json ld flavor of variants
                if isinstance(offer.get("name"), str) and offer["name"].strip():
                    out.setdefault("variants", []).append(
                        {"name": _unesc(offer["name"])[:80], "price": p,
                         "compare_at": None})
    # several distinct offer prices is ambiguity not an answer
    prices = out.pop("prices", set())
    if len(prices) == 1:
        out["price"] = prices.pop()
    elif len(prices) > 1:
        out["conflict"] = True
    return {k: v for k, v in out.items() if v}


# extract the main product from framework state
# the hint is the page title and breaks ties against recommended products
def state(objs: list, hint: str = "") -> dict:
    hint_toks = set(re.findall(r"[a-z0-9]+", hint.lower()))
    best, best_score, cur_seen = {}, 0, None
    imgs_seen: list[str] = []

    def walk(o, shopify: bool, depth: int):
        nonlocal best, best_score, cur_seen
        if depth > 14:
            return
        if isinstance(o, list):
            for v in o[:120]:
                walk(v, shopify, depth + 1)
            return
        if not isinstance(o, dict):
            return
        # shopify product json is cents encoded and handle is the tell
        shopify = shopify or "handle" in o or "compare_at_price" in o
        cand = _mine_dict(o, shopify)
        cur_seen = cur_seen or cand.get("currency")
        for k, v in o.items():
            if k.lower() in ("imageurl", "mainimage", "image") and isinstance(v, str) \
                    and "//" in v and len(imgs_seen) < 10:
                imgs_seen.append("https:" + v if v.startswith("//") else v)
        if cand.get("name") and "price" in cand:
            toks = set(re.findall(r"[a-z0-9]+", str(cand["name"]).lower()))
            # core fields plus title overlap win and parent objects beat their own variants
            score = sum(k in cand for k in ("name", "price", "compare_at", "currency"))                 + 2 * len(toks & hint_toks) + 2 * ("variants" in o)
            if score > best_score:
                best, best_score = cand, score
        for v in o.values():
            walk(v, shopify, depth + 1)

    walk(objs, False, 0)
    if cur_seen:
        best.setdefault("currency", cur_seen)
    if imgs_seen and not best.get("images"):
        best["images"] = list(dict.fromkeys(imgs_seen))
    crumbs = _crumbs(objs)
    if crumbs:
        best.setdefault("crumbs", crumbs)
    return best


# pull typed fields out of one dict by key name
def _mine_dict(o: dict, shopify: bool) -> dict:
    out: dict = {}
    for k, v in o.items():
        lk = k.lower().replace("-", "_")
        if lk in _NAME_KEYS and isinstance(v, str) and 3 <= len(v) <= 150:
            out.setdefault("name", _unesc(v))
        elif lk in _PRICE_KEYS:
            out.setdefault("price", _cents(_num(v), v, shopify))
        elif lk in _COMPARE_KEYS:
            out.setdefault("compare_at", _cents(_num(v), v, shopify))
        elif lk in ("images", "media") and isinstance(v, list):
            urls = []
            for it in v[:12]:
                if isinstance(it, dict):
                    it = it.get("src") or it.get("url") or (it.get("preview_image") or {}).get("src")
                if isinstance(it, str) and ("//" in it):
                    urls.append("https:" + it if it.startswith("//") else it)
            if urls:
                out.setdefault("images", urls)
        elif lk in _CURRENCY_KEYS:
            if isinstance(v, dict):
                v = v.get("active") or v.get("code") or v.get("isoCode") or ""
            if isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v):
                out.setdefault("currency", v)
    # shopify keeps the real prices and the option matrix on the variants
    variants = o.get("variants")
    if isinstance(variants, list) and variants and isinstance(variants[0], dict):
        vr = variants[0]
        for key, field in (("price", "price"), ("compare_at_price", "compare_at")):
            if key in vr:
                out[field] = _cents(_num(vr[key]), vr[key], True)
        vs = [v for v in (_variant(x) for x in variants[:30]) if v]
        if vs:
            out["variants"] = vs
        # some shopify product dicts carry no title of their own, the
        # variants hold the full name so borrow it or the parent never wins
        if "name" not in out:
            full = vr.get("name") or vr.get("title")
            if isinstance(full, str) and len(full) >= 3:
                out["name"] = _unesc(full)[:150]
    return {k: v for k, v in out.items()
            if v is not None and (k in ("name", "currency", "variants", "images")
                                  or _ok_price(v))}


# one discrete configuration of the product, like a size or color
def _variant(vr) -> dict | None:
    if not isinstance(vr, dict):
        return None
    name = vr.get("title") or vr.get("public_title") or " / ".join(
        str(vr[k]) for k in ("option1", "option2", "option3") if vr.get(k))
    if not isinstance(name, str) or not name.strip():
        return None
    v = {"name": _unesc(name)[:80],
         "price": _cents(_num(vr.get("price")), vr.get("price"), True),
         "compare_at": _cents(_num(vr.get("compare_at_price")),
                              vr.get("compare_at_price"), True)}
    if vr.get("available") is not None:
        v["available"] = bool(vr["available"])
    return v


# an integer 8940 in a shopify blob means 89.40
def _cents(n: float | None, raw, shopify: bool) -> float | None:
    if n is not None and shopify and isinstance(raw, int) and n >= 100:
        return n / 100
    return n


# collect category style strings anywhere in the state
def _crumbs(objs, depth: int = 0) -> list[str]:
    found: list[str] = []
    if depth > 10:
        return found
    if isinstance(objs, list):
        for v in objs[:60]:
            found += _crumbs(v, depth + 1)
    elif isinstance(objs, dict):
        for k, v in objs.items():
            if k.lower().replace("-", "_") in _CRUMB_KEYS and isinstance(v, str) and v:
                found.append(v[:60])
            else:
                found += _crumbs(v, depth + 1)
    return found[:6]
