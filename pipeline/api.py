"""
the demo front door on fly
post any product url and get a product back with per field provenance
every request lands in a log so stats can tell the story
  run    fetch then extract one url and log the row
  stats  success rate rung attribution latency and tokens from the log
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
