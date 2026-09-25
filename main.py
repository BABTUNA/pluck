"""hydrate the product schema from every page in data/

    uv run python main.py
"""

import asyncio
import json
import logging
from pathlib import Path

import models
from pluck.extract import extract

logging.basicConfig(level=logging.INFO, format="%(message)s")
OUT = Path("output")


async def one(page: Path) -> int:
    html = page.read_text(errors="ignore")
    raw = await extract(html)
    product = models.from_pluck(raw)  # their schema, category validator included
    OUT.mkdir(exist_ok=True)
    (OUT / f"{page.stem}.json").write_text(product.model_dump_json(indent=1))
    tokens = (raw.meta["llm_tokens"] or {}).get("total_tokens", 0)
    cost = (raw.meta["llm_tokens"] or {}).get("cost", 0) or 0
    logging.info(f"{page.stem:12} {raw.meta['latency_s']:>5}s  {tokens:>6} tokens  "
                 f"${cost:.5f}  sources={raw.meta['sources']}")
    return tokens


async def main():
    pages = sorted(Path("data").glob("*.html"))
    totals = await asyncio.gather(*(one(p) for p in pages))
    per_page = sum(totals) / max(len(pages), 1)
    logging.info(f"\n{len(pages)} pages, {sum(totals)} tokens total")
    # the extrapolation ai.py's _log_usage prints, for the whole pipeline
    for scale in (1_000_000, 10_000_000):
        est = per_page * scale / 1e6 * 0.10  # flash-lite input rate dominates
        logging.info(f"  ~${est:,.0f} for {scale:,} products")


if __name__ == "__main__":
    asyncio.run(main())
