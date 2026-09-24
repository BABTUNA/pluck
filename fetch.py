"""Fetch a live product page. Realistic headers, one retry, honest errors:
a block is data for the analysis, not something to hide.
"""

import asyncio

import httpx

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch(url: str) -> tuple[str | None, str | None]:
    """Returns (html, error). Exactly one is set."""
    last = "unknown"
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True,
                                         timeout=20) as client:
                r = await client.get(url)
            if r.status_code == 200 and "<html" in r.text[:2000].lower():
                return r.text, None
            last = f"http {r.status_code}" if r.status_code != 200 else "not html"
        except httpx.HTTPError as e:
            last = type(e).__name__
        await asyncio.sleep(1 + attempt)
    return None, last
