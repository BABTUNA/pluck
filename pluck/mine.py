"""One miner, many sources. Every rung produces JSON; this walks it for
product fields so rungs stay dumb harvesters and the tree stays readable.
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
    return _html.unescape(_html.unescape(s)).strip()  # pages double-encode


def _num(v) -> float | None:
    """Numbers arrive as 129.9, '129.90', '$129.90', or money dicts."""
    if isinstance(v, dict):
        v = v.get("amount") or v.get("value") or v.get("price")
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d.,]", "", v)
        if s.count(",") == 1 and re.search(r",\d{2}$", s):
            s = s.replace(".", "").replace(",", ".")  # 1.299,90 eu style
        else:
            s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _ok_price(v: float | None) -> bool:
    return v is not None and 0.5 <= v <= 500_000


# ---------------------------------------------------------------- json-ld --
def jsonld(objs: list) -> dict:
    """Mine schema.org Product objects: the merchant's declared answer."""
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
    # several distinct offer prices means the merchant declared an ambiguity,
    # not an answer; leave price unset so the tree climbs
    prices = out.pop("prices", set())
    if len(prices) == 1:
        out["price"] = prices.pop()
    elif len(prices) > 1:
        out["conflict"] = True
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------- app state (shipped or vm) --
def state(objs: list, hint: str = "") -> dict:
    """Mine framework state for the product the page is about. `hint` (page
    title words) breaks ties against recommended-product entries."""
    hint_toks = set(re.findall(r"[a-z0-9]+", hint.lower()))
    best, best_score = {}, 0

    def walk(o, shopify: bool, depth: int):
        nonlocal best, best_score
        if depth > 14:
            return
        if isinstance(o, list):
            for v in o[:120]:
                walk(v, shopify, depth + 1)
            return
        if not isinstance(o, dict):
            return
        # shopify's .js product json is cents-encoded; "handle" is the tell
        shopify = shopify or "handle" in o or "compare_at_price" in o
        cand = _mine_dict(o, shopify)
        if cand.get("name") and "price" in cand:
            toks = set(re.findall(r"[a-z0-9]+", str(cand["name"]).lower()))
            # parent product objects beat their own variant rows
            score = len(cand) + 2 * len(toks & hint_toks) + 2 * ("variants" in o)
            if score > best_score:
                best, best_score = cand, score
        for v in o.values():
            walk(v, shopify, depth + 1)

    walk(objs, False, 0)
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
        elif lk in _CURRENCY_KEYS and isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v):
            out.setdefault("currency", v)
    # variant lists carry the real prices on shopify-shaped objects
    for vr in (o.get("variants") or [])[:1] if isinstance(o.get("variants"), list) else []:
        if isinstance(vr, dict):
            for k, fk in (("price", "price"), ("compare_at_price", "compare_at")):
                if k in vr:
                    out[fk] = _cents(_num(vr[k]), vr[k], True)
    return {k: v for k, v in out.items()
            if v is not None and (k in ("name", "currency") or _ok_price(v))}


def _cents(n: float | None, raw, shopify: bool) -> float | None:
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
