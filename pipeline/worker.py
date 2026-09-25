"""
a worker that claims fetches extracts and stores forever
stateless and identical to every other worker so throughput scales by starting more
  run_one   process one claimed job end to end
  discover  find same domain product links to feed back into the queue
  loop      claim then work then repeat and back off when the queue is empty
run with python -m pipeline.worker
"""

import asyncio
import json
import os
import re
import socket

from pipeline import jobq as q
from pipeline.fetch import fetch
from pluck.extract import extract

WORKER = os.environ.get("FLY_MACHINE_ID", socket.gethostname())
CONCURRENCY = int(os.environ.get("PLUCK_CONCURRENCY", "3"))
_LINK = re.compile(r'href=["\']([^"\'#?]*/products?/[^"\'#?]+)["\']', re.I)


# find same domain product links on the page to feed the queue
def discover(url: str, html: str) -> list[str]:
    host = url.split("/")[2]
    out = []
    for m in _LINK.finditer(html):
        link = m.group(1)
        if link.startswith("/"):
            link = f"https://{host}{link}"
        # api routes and feeds match the product pattern but are not pages
        if re.search(r"wp-json|oembed|\.(js|css|json|xml|png|jpg)($|\?)", link, re.I):
            continue
        if link.split("/")[2] == host:
            out.append(link)
    return out[:25]


# process one job end to end and report the outcome
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


# claim then work then repeat and back off when the queue is empty
# a db blip must never kill the loop, the lease system already covers the job
async def loop(pool):
    idle = 0
    while True:
        try:
            if await q.get_flag(pool, "paused") == "1":
                await asyncio.sleep(10)
                continue
            job = await q.claim(pool)
            if job is None:
                idle += 1
                await asyncio.sleep(min(30, 2 * idle))  # queue empty: back off
                continue
            idle = 0
            await run_one(pool, job)
        except Exception as e:  # noqa: BLE001
            print(f"[{WORKER}] loop error {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(5)
        await asyncio.sleep(1)  # per-task politeness between fetches


# n concurrent loops sharing one pool
async def main():
    pool = await q.connect()
    print(f"[{WORKER}] up, concurrency {CONCURRENCY}", flush=True)
    await asyncio.gather(*(loop(pool) for _ in range(CONCURRENCY)))


if __name__ == "__main__":
    asyncio.run(main())
