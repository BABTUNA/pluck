"""A worker: claim -> fetch -> extract -> store, forever. Stateless and
identical to every other worker, so throughput scales by starting more.

    python worker.py
"""

import asyncio
import json
import os
import re
import socket

import jobq as q
from fetch import fetch
from pluck.extract import extract

WORKER = os.environ.get("FLY_MACHINE_ID", socket.gethostname())
CONCURRENCY = int(os.environ.get("PLUCK_CONCURRENCY", "3"))
_LINK = re.compile(r'href=["\']([^"\'#?]*/products?/[^"\'#?]+)["\']', re.I)


def discover(url: str, html: str) -> list[str]:
    """same-domain product links on the page feed the queue"""
    host = url.split("/")[2]
    out = []
    for m in _LINK.finditer(html):
        link = m.group(1)
        if link.startswith("/"):
            link = f"https://{host}{link}"
        if link.split("/")[2] == host and not re.search(r"\.(js|css|json|png|jpg)$", link):
            out.append(link)
    return out[:25]


async def run_one(pool, job) -> None:
    html, err = await fetch(job["url"])
    if err:
        await q.fail(pool, job["id"], job["url"], f"fetch: {err}")
        print(f"[{WORKER}] fail  {job['url']} ({err})", flush=True)
        return
    try:
        product = await extract(html)
    except Exception as e:  # noqa: BLE001
        await q.fail(pool, job["id"], job["url"], f"extract: {type(e).__name__}: {e}")
        print(f"[{WORKER}] fail  {job['url']} ({type(e).__name__})", flush=True)
        return
    await q.done(pool, job["id"], job["url"], product.model_dump_json(), WORKER)
    n = await q.enqueue(pool, discover(job["url"], html))
    print(f"[{WORKER}] done  {job['url']}  "
          f"price={product.price.value}({product.price.source})  +{n} discovered", flush=True)


async def loop(pool):
    idle = 0
    while True:
        job = await q.claim(pool, WORKER)
        if job is None:
            idle += 1
            await asyncio.sleep(min(30, 2 * idle))  # queue empty: back off
            continue
        idle = 0
        await run_one(pool, job)
        await asyncio.sleep(1)  # per-task politeness between fetches


async def main():
    pool = await q.connect()
    print(f"[{WORKER}] up, concurrency {CONCURRENCY}", flush=True)
    await asyncio.gather(*(loop(pool) for _ in range(CONCURRENCY)))


if __name__ == "__main__":
    asyncio.run(main())
