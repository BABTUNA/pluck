"""Sweep a page into typed candidates. Deterministic, site-agnostic, fast.

Candidates are short strings with just enough context for a model to choose
between them by letter. Finding is code's job; choosing is the model's.
"""

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

_MONEY = re.compile(
    r"(?:(?P<sym>[$€£¥])\s?(?P<a>\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{2})?)"
    r"|(?P<b>\d{1,5}(?:[.,]\d{2})?)\s?(?P<code>USD|EUR|GBP|SEK|DKK|NOK|CAD|AUD|JPY|kr|zł|[$€£¥]))"
)
_PRICE_KEY = re.compile(
    r'"[^"]*price[^"]*"\s*:\s*"?(\d{1,6}(?:[.,]\d{1,2})?)"?', re.I)
_CUR_HINTS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "kr": "SEK", "zł": "PLN"}


@dataclass
class Candidates:
    names: list[str] = field(default_factory=list)
    prices: list[str] = field(default_factory=list)       # "129.90 [context]"
    currencies: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)       # "url | alt"
    categories: list[str] = field(default_factory=list)
    title: str = ""
    breadcrumb: str = ""


def _norm_amount(raw: str) -> float | None:
    s = raw.strip().replace(" ", "")
    # 1.299,00 (eu) vs 1,299.00 (us) vs 129,90 vs 129.90
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if 0.5 <= v <= 500_000 else None


def sweep(html: str) -> Candidates:
    soup = BeautifulSoup(html, "lxml")
    c = Candidates()

    # -- identity-ish text sources
    if soup.title and soup.title.string:
        c.title = soup.title.string.strip()[:150]
    h1 = soup.find("h1")
    og = {m.get("property") or m.get("name"): m.get("content")
          for m in soup.find_all("meta") if m.get("content")}

    for s in [h1.get_text(" ", strip=True) if h1 else None, og.get("og:title"), c.title]:
        if s and s.strip() and s.strip()[:90] not in c.names:
            c.names.append(s.strip()[:90])

    # -- structured blocks: json-ld and any parseable script json
    blobs: list[str] = []
    for tag in soup.find_all("script"):
        body = tag.string or ""
        if not body or len(body) < 50:
            continue
        t = (tag.get("type") or "").lower()
        if "json" in t:
            blobs.append(body[:150_000])
        elif re.search(r"__NEXT_DATA__|__NUXT|__INITIAL|__SERVER_DATA__|__PRELOADED|__remix|var\s+meta\s*=", body):
            blobs.append(body[:150_000])
        if t == "application/ld+json":
            m = re.search(r'"name"\s*:\s*"([^"]{4,90})"', body)
            if m and m.group(1) not in c.names:
                c.names.append(m.group(1))

    # -- visible text (script/style stripped)
    for el in soup.find_all(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        el.decompose()
    text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))[:10_000]

    # -- price candidates from money patterns + price-named json keys
    seen: dict[str, str] = {}
    for src in [text] + blobs:
        for m in _MONEY.finditer(src):
            v = _norm_amount(m.group("a") or m.group("b") or "")
            if v is None:
                continue
            key = f"{v:g}"
            ctx = re.sub(r"\s+", " ", src[max(0, m.start() - 55):m.end() + 25])
            seen.setdefault(key, ctx[:90])
            sym = m.group("sym") or m.group("code") or ""
            cur = _CUR_HINTS.get(sym, sym if len(sym) == 3 else "")
            if cur and cur not in c.currencies:
                c.currencies.append(cur)
        for m in _PRICE_KEY.finditer(src):
            v = _norm_amount(m.group(1))
            if v is None:
                continue
            key = f"{v:g}"
            ctx = re.sub(r"\s+", " ", src[max(0, m.start() - 40):m.end() + 20])
            seen.setdefault(key, ctx[:90])
    c.prices = [f"{k}  [{ctx}]" for k, ctx in list(seen.items())[:9]]
    for code in re.findall(r'"(?:priceCurrency|currency(?:Code)?)"\s*:\s*"([A-Z]{3})"', " ".join(blobs)):
        if code not in c.currencies:
            c.currencies.append(code)
    c.currencies = c.currencies[:4]

    # -- image candidates: og + largest-looking imgs with alt
    imgs: dict[str, str] = {}
    if og.get("og:image"):
        imgs[og["og:image"]] = "og:image hero"
    for im in soup.find_all("img", limit=200):
        src = im.get("src") or ""
        if src.startswith("http") and not re.search(r"logo|icon|sprite|pixel|\.svg", src, re.I):
            imgs.setdefault(src, (im.get("alt") or "")[:60])
        if len(imgs) >= 9:
            break
    c.images = [f"{u[:110]} | {alt}" for u, alt in imgs.items()]

    # -- breadcrumb text (category signal)
    m = re.search(r'"BreadcrumbList".{0,1500}', html)
    if m:
        c.breadcrumb = " > ".join(re.findall(r'"name"\s*:\s*"([^"]{2,40})"', m.group(0))[:6])

    return c
