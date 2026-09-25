"""
the demo front door on fly
post any product url and get a product back with per field provenance
also serves the storefront ui straight from the crawl database
  run       fetch then extract one url and log the row
  stats     success rate rung attribution latency and tokens from the log
  products  the crawled catalog newest first
  product   one crawled product by id
  progress  queue counts for the live extraction view
run with uvicorn pipeline.api:app --port 8080
"""

import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from pipeline.fetch import fetch
from pluck.extract import extract

app = FastAPI(title="pluck")
LOG = Path(os.environ.get("PLUCK_LOG", "requests.jsonl"))
TOKEN = os.environ.get("PLUCK_TOKEN")  # unset = open


# the request body
class Job(BaseModel):
    url: str


# fetch then extract one url and append the outcome to the log
@app.post("/extract")
async def run(job: Job, authorization: str | None = Header(None)):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401)
    t0 = time.time()
    html, err = await fetch(job.url)
    row = {"url": job.url, "ts": int(t0), "fetch_s": round(time.time() - t0, 2)}
    if err:
        row["error"] = f"fetch: {err}"
    else:
        try:
            p = await extract(html)
            row |= {"product": p.model_dump(), "total_s": round(time.time() - t0, 2)}
        except Exception as e:  # noqa: BLE001 - log it, return it, move on
            row["error"] = f"extract: {type(e).__name__}: {e}"
    with LOG.open("a") as f:
        f.write(json.dumps(row) + "\n")
    if "error" in row:
        raise HTTPException(422, row["error"])
    return row


# crunch the request log into the numbers that matter
@app.get("/stats")
async def stats():
    rows = [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []
    done = [r for r in rows if "product" in r]
    sources, tokens = {}, 0
    for r in done:
        for k, s in r["product"]["meta"]["sources"].items():
            sources.setdefault(k, {}).setdefault(s, 0)
            sources[k][s] += 1
        tokens += (r["product"]["meta"]["llm_tokens"] or {}).get("total_tokens", 0)
    lat = sorted(r["total_s"] for r in done)
    return {
        "pages": len(rows),
        "extracted": len(done),
        "fetch_blocked_or_failed": len(rows) - len(done),
        "field_sources": sources,
        "llm_tokens": tokens,
        "latency_p50_s": lat[len(lat) // 2] if lat else None,
        "latency_p95_s": lat[int(len(lat) * 0.95)] if lat else None,
    }


# ---- the storefront reads the crawl database ------------------------------

import asyncpg
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_pool = None


# one lazy pool shared by the catalog endpoints
async def _db():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    return _pool


# shape one results row the way the storefront expects a summary card
def _summary(r) -> dict:
    p = json.loads(r["product"])
    imgs = p.get("images") or []
    return {
        "id": r["id"],
        "name": p["name"]["value"] or r["url"].split("/")[-1],
        "brand": r["url"].split("/")[2].removeprefix("www."),
        "price": {"price": p["price"]["value"] or 0,
                  "currency": p["currency"]["value"] or "USD",
                  "compare_at_price": p["compare_at"]["value"]},
        "image_url": imgs[0] if imgs else None,
        "hover_image_url": imgs[1] if len(imgs) > 1 else None,
        "category": (p["category"]["value"] or "").split(" > ")[-1],
        "source": r["batch"],
    }


# the crawled catalog newest first, batch picks which collection
@app.get("/products")
async def products(batch: str = "all"):
    where = {"five": "batch = 'assignment5'",
             "fifty": "batch IN ('assignment5', 'assignment')",
             "live": "batch = 'live'"}.get(batch, "true")
    pool = await _db()
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT md5(url) AS id, url, product, batch, processed_at FROM results "
            "WHERE product->'price'->>'value' IS NOT NULL "  # no price means the page gave us nothing worth showing
            f"AND {where} ORDER BY processed_at DESC LIMIT 500")
    out, seen = [], set()
    for r in rows:
        s = _summary(r)
        # different variant urls of the same product make ugly duplicate cards
        key = (s["brand"], s["name"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# one crawled product with per field provenance
@app.get("/products/{pid}")
async def product(pid: str):
    pool = await _db()
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT md5(url) AS id, url, product, worker, processed_at "
            "FROM results WHERE md5(url) = $1", pid)
    if r is None:
        raise HTTPException(404)
    p = json.loads(r["product"])
    return {
        "id": r["id"],
        "url": r["url"],
        "name": p["name"]["value"],
        "price": {"price": p["price"]["value"] or 0,
                  "currency": p["currency"]["value"] or "USD",
                  "compare_at_price": p["compare_at"]["value"]},
        "image_urls": p.get("images") or [],
        "category": p["category"]["value"],
        "sources": p["meta"]["sources"],
        "latency_s": p["meta"]["latency_s"],
        "worker": r["worker"],
        "processed_at": r["processed_at"].isoformat(),
    }


# queue counts for the live extraction progress bar
@app.get("/progress")
async def progress():
    pool = await _db()
    async with pool.acquire() as c:
        counts = {r["status"]: r["n"] for r in
                  await c.fetch("SELECT status, count(*) n FROM jobs GROUP BY status")}
        dead = await c.fetchval("SELECT count(*) FROM dead_letters")
    done = counts.get("done", 0)
    total = sum(counts.values()) + dead
    return {"done": done, "queued": counts.get("queued", 0),
            "leased": counts.get("leased", 0), "dead": dead, "total": total}


# serve the built storefront when it exists
_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        f = _dist / path
        return FileResponse(f if f.is_file() else _dist / "index.html")
