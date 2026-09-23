"""Grade pluck against the previous project's verified outputs on 50 pages.

Usage: uv run python eval.py [n_pages]
"""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from pluck.extract import extract

OLD = Path("/Users/benba/projects/interviews/take-home-2026")


async def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 999
    pages = (sorted((OLD / "data").glob("*.html"))
             + sorted((OLD / "data_unseen").glob("*.html")))[:limit]
    expected_cats = json.loads((OLD / "eval" / "expected_categories.json").read_text())

    score = {k: [0, 0] for k in ("name", "price", "compare_at", "currency", "category")}
    sources, misses = Counter(), []
    total_tokens = 0

    sem = asyncio.Semaphore(8)

    async def one(p):
        async with sem:
            try:
                return p.stem, await extract(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception as e:  # noqa: BLE001
                return p.stem, e

    for stem, r in await asyncio.gather(*(one(p) for p in pages)):
        if isinstance(r, Exception):
            misses.append(f"{stem}: ERROR {r!r}")
            continue
        ref_file = OLD / "output" / f"{stem}.json"
        if not ref_file.exists():
            continue
        ref = json.loads(ref_file.read_text())
        total_tokens += (r.meta["llm_tokens"] or {}).get("total_tokens", 0)

        def num_eq(a, b):
            return a is not None and b is not None and abs(float(a) - float(b)) < 0.01

        truth = {
            "name": lambda v: bool(v) and (
                str(v).lower().startswith(ref["name"].lower()[:25])
                or ref["name"].lower().startswith(str(v).lower()[:25])),
            "price": lambda v: num_eq(v, ref["price"]["price"]),
            "compare_at": lambda v: (v is None and ref["price"]["compare_at_price"] is None)
                                    or num_eq(v, ref["price"]["compare_at_price"]),
            "currency": lambda v: v == ref["price"]["currency"],
            "category": lambda v: v in expected_cats.get(stem, []),
        }
        for k, check in truth.items():
            f = getattr(r, k)
            ok = check(f.value)
            score[k][1] += 1
            score[k][0] += ok
            sources[(k, f.source, "ok" if ok else "MISS")] += 1
            if not ok:
                misses.append(f"{stem}.{k}: {f.value!r} ({f.source})")

    print(f"\n== pluck decision tree vs verified pipeline ({score['name'][1]} pages) ==")
    for k, (c, n) in score.items():
        print(f"  {k:11} {c}/{n}  ({c / max(n, 1):.0%})")
    print("\nrung attribution (field, source -> ok/miss):")
    for k in score:
        row = {s: [0, 0] for s in ("declared", "shipped", "computed", "inferred", "none")}
        for (fk, src, ok), n in sources.items():
            if fk == k:
                row[src][ok == "ok"] += n
        cells = "  ".join(f"{s}:{good}/{good + bad}" for s, (bad, good) in row.items()
                          if good + bad)
        print(f"  {k:11} {cells}")
    print(f"\n  llm tokens total: {total_tokens}  "
          f"(~${total_tokens / 1e6 * 0.10:.4f} at flash-lite rates)")
    print(f"\nmisses ({len(misses)}):")
    for m in misses[:30]:
        print("  " + m)


asyncio.run(main())
