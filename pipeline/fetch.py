"""
fetch a live product page with realistic headers and honest errors
a block is data for the analysis not something to hide
  fetch    get a url with one retry and return (html, error), exactly one set
  blocked  reject urls that point anywhere internal
"""

import asyncio

import httpx

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# get the page with one retry and return (html, error) where exactly one is set
# user supplied urls must not reach anything internal, literal hosts and
# their dns answers are checked, a redirect chain is not
def blocked(url: str) -> str | None:
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    if len(url) > 2000:
        return "url too long"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "bad url"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "http(s) urls only"
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return "blocked host"
    try:
        ip = ipaddress.ip_address(host)
        return None if ip.is_global else "blocked host"
    except ValueError:
        pass  # a normal hostname, check what it resolves to
    try:
        infos = socket.getaddrinfo(host, None)
        if any(not ipaddress.ip_address(i[4][0]).is_global for i in infos):
            return "blocked host"
    except (OSError, ValueError):
        return "unresolvable host"
    return None


# get the page with one retry and return (html, error) where exactly one is set
async def fetch(url: str) -> tuple[str | None, str | None]:
    if err := blocked(url):
        return None, err
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
