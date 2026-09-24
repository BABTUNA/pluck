"""Seed the queue with real product urls from each store's public sitemap,
then let worker discovery grow the frontier.

    DATABASE_URL=... python -m pipeline.seed
"""

import asyncio
import re

import httpx

from pipeline import jobq as q
from pipeline.fetch import HEADERS

STORES = [
    "www.allbirds.com", "www.brooklinen.com", "www.gymshark.com", "bombas.com",
    "ruggable.com", "mejuri.com", "goodr.com", "www.peakdesign.com",
    "www.deathwishcoffee.com", "rothys.com", "colourpop.com", "www.spigen.com",
    "www.tentree.com", "kotn.com", "www.jarsofdust.com", "porterandyork.com",
    "shop.mattel.com", "www.dossier.co", "us.bellroy.com", "www.thorne.com",
]
PER_STORE = 3


async def store_products(client, domain: str) -> list[str]:
    try:
        r = await client.get(f"https://{domain}/sitemap.xml")
        sub = re.findall(r"<loc>([^<]*sitemap_products[^<]*)</loc>", r.text) or \
              re.findall(r"<loc>([^<]*product[^<]*\.xml)</loc>", r.text)
        if sub:
            r = await client.get(sub[0].strip())
        urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", r.text)
                if re.search(r"/products?/", u)]
        return urls[:PER_STORE]
    except httpx.HTTPError:
        return []


async def main():
    pool = await q.connect()
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as c:
        found = await asyncio.gather(*(store_products(c, d) for d in STORES))
    for d, urls in zip(STORES, found):
        n = await q.enqueue(pool, urls)
        print(f"{d}: {len(urls)} found, {n} enqueued")


if __name__ == "__main__":
    asyncio.run(main())
