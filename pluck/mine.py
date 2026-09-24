"""
one miner, many sources: every rung produces json, this walks it for product fields
rungs stay dumb harvesters and the tree stays readable
  jsonld  mine schema org product objects, the merchants declared answer
  state   mine framework state for the product the page is about
  _num    money arrives as 129.9, "129.90", "$129.90", cents ints or amount dicts
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


def _unesc(s: str) -> str:
    # pages double encode, unescape twice
    return _html.unescape(_html.unescape(s)).strip()


def _num(v) -> float | None:
    if isinstance(v, dict):
        v = v.get("amount") or v.get("value") or v.get("price")
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d.,]", "", v)
        # 1.299,90 european style vs 1,299.90
        if s.count(",") == 1 and re.search(r",\d{2}$", s):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _ok_price(v: float | None) -> bool:
    return v is not None and 0.5 <= v <= 500_000


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
    # several distinct offer prices is an ambiguity declared, not an answer
    prices = out.pop("prices", set())
    if len(prices) == 1:
        out["price"] = prices.pop()
    elif len(prices) > 1:
        out["conflict"] = True
    return {k: v for k, v in out.items() if v}


def state(objs: list, hint: str = "") -> dict:
    # hint is the page title, it breaks ties against recommended product entries
    hint_toks = set(re.findall(r"[a-z0-9]+", hint.lower()))
    best, best_score, cur_seen = {}, 0, None

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
        # shopifys product json is cents encoded, handle is the tell
        shopify = shopify or "handle" in o or "compare_at_price" in o
        cand = _mine_dict(o, shopify)
        cur_seen = cur_seen or cand.get("currency")
        if cand.get("name") and "price" in cand:
            toks = set(re.findall(r"[a-z0-9]+", str(cand["name"]).lower()))
            # more filled fields, title overlap, and parent objects beat their own variants
            score = len(cand) + 2 * len(toks & hint_toks) + 2 * ("variants" in o)
            if score > best_score:
                best, best_score = cand, score
        for v in o.values():
            walk(v, shopify, depth + 1)

    walk(objs, False, 0)
    if cur_seen:
        best.setdefault("currency", cur_seen)
    crumbs = _crumbs(objs)
    if crumbs:
        best.setdefault("crumbs", crumbs)
    return best


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
        elif lk in _CURRENCY_KEYS:
            if isinstance(v, dict):
                v = v.get("active") or v.get("code") or v.get("isoCode") or ""
            if isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v):
                out.setdefault("currency", v)
    # shopify keeps the real prices on the variants
    variants = o.get("variants")
    if isinstance(variants, list) and variants and isinstance(variants[0], dict):
        vr = variants[0]
        for key, field in (("price", "price"), ("compare_at_price", "compare_at")):
            if key in vr:
                out[field] = _cents(_num(vr[key]), vr[key], True)
    return {k: v for k, v in out.items()
            if v is not None and (k in ("name", "currency") or _ok_price(v))}


def _cents(n: float | None, raw, shopify: bool) -> float | None:
    # an integer 8940 in a shopify blob means 89.40
    if n is not None and shopify and isinstance(raw, int) and n >= 100:
        return n / 100
    return n


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
