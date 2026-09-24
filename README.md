# pluck

Extract product data (name, price, compare-at, currency, category, images) from any product page. One decision tree: the page's own data answers first, a model only speaks when the page can't.

It is deployed. Try any product URL:

```bash
curl -X POST https://pluck-extract.fly.dev/extract \
  -H 'content-type: application/json' \
  -d '{"url": "https://www.brooklinen.com/products/luxe-core-sheet-set"}'
```

Deep dives: [EXTRACTION.md](docs/EXTRACTION.md) for the decision tree, [PIPELINE.md](docs/PIPELINE.md) for the distributed crawler and deployment.

## Goal and how it works

Most product pages already contain the answer in machine-readable form. Pluck climbs four rungs, cheapest first, and stops as soon as the core fields (name, price, currency) are filled:

1. **declared** - JSON-LD blocks the merchant wrote for Google. Free, exact.
2. **shipped** - JSON state frameworks embed as inert script tags (`__NEXT_DATA__` and friends). Free.
3. **computed** - run the page's own inline JS in a V8 sandbox and read the state it builds. Free, catches Shopify-style pages that build state at runtime.
4. **inferred** - one small LLM call over the cleaned page text for whatever is still missing.

Two guard rules make it honest:

- A deterministic price must also appear on the visible page. If the page's data says 299.95 but the rendered page shows 279.95, the rungs disagree and the model referees. Same when two rungs disagree with each other.
- Category is never on the page, so it always uses the model, as a descent of the taxonomy tree: pick 1 of 21 top-level categories (rides along on the field call), then pick the exact path from only that branch's real subtree. The model can only ever answer with a real taxonomy string.

Example, a Shopify robe page with no JSON-LD offers: rung 1 gives the name, rungs 1-2 have no price, rung 3 executes the page's scripts and finds `product.variants[0].price: 8940` (cents, divided to 89.40), the visible-price check confirms it, and the model only gets asked for the category. Cost of the whole page: two sub-cent calls.

## Results

Against 50 verified pages (same eval set as the previous full-pipeline project):

| field | pluck (flash-lite) | pluck (gemini-3-flash) | previous pipeline |
|---|---|---|---|
| name | 96% | 96% | ~98% |
| price | 96% | 96% | ~96% |
| compare-at | 90-94% | 92% | ~94% |
| currency | 100% | 100% | - |
| category | 80% | 92% | 96% |
| cost per 1K pages | ~$1 | ~$6 | $20 |
| code | 634 lines | same | ~2,400 lines |

Measured in production (311 live pages, 18 stores, one crawl):

- **$0.71 per 1K pages** (actual OpenRouter billing), latency p50 1.5s / p95 2.5s
- name resolved free on 100% of pages, currency 83%, price 72%; category always uses the model by design
- 77% fetch success; failures are bot-walled stores, each dead-lettered with its reason

## Call trace

```
extract(html)                          climbs the rungs, assembles the product      pluck/extract.py
├─ rungs.scripts(html)                 pulls every inline <script> body             pluck/rungs.py
├─ mine.jsonld(rungs.declared(scr))    reads the merchant's json-ld for google      pluck/mine.py
├─ mine.state(rungs.shipped(scr))      parses embedded json state (__NEXT_DATA__)   pluck/mine.py
├─ mine.state(rungs.computed(scr))     runs page js in a v8 sandbox, reads state    pluck/rungs.py
├─ _visible_prices(html)               price must show on the page or model referees pluck/extract.py
├─ infer(html, missing, TOPS, known)   one call: missing fields + top-level category pluck/infer.py
├─ pick_leaf(known, html, subtree)     one call: exact path within that branch      pluck/infer.py
└─ taxonomy.snap(answer)               snaps any stray answer to a real path        pluck/taxonomy.py
```

## The distributed pipeline

The deployed system is a crawler x extractor with real big-data mechanics, run at demo scale:

```
seed.py ── real product urls from store sitemaps ──▶ Postgres jobs table
                                                       │
   worker × N (fly machines, identical, stateless) ◀───┘
   claim with a 2-min lease ─ fetch ─ extract ─ store result
   ├─ crash: lease expires, another worker reclaims the job
   ├─ failure: retry with exponential backoff (1m, 4m, 16m), then dead-letter
   └─ discovery: same-domain product links go back into the queue (capped per domain)
```

- Workers coordinate only through atomic claims (`FOR UPDATE SKIP LOCKED`); duplicates are impossible by construction (`url` is unique, inserts are `ON CONFLICT DO NOTHING` on normalized urls).
- Measured scaling: 9 pages/min at 1 worker, 21 pages/min at 4, changed with one command (`fly scale count worker=4`). A worker did freeze mid-crawl once; its leased pages were reclaimed automatically and nothing was lost.
- At 50M products the shape stays the same and the parts grow: partitioned job/result storage, per-domain rate-limit coordination, a headless-browser fetch tier for the stores that ship empty HTML, and re-crawl scheduling off the `processed_at` column that already exists.

## Files and data structures

| file | what it does |
|---|---|
| `pluck/extract.py` | the router: climbs rungs, detects conflicts, assembles the `Product` |
| `pluck/rungs.py` | the three deterministic harvesters, all returning parsed JSON objects |
| `pluck/mine.py` | one miner that walks any JSON for product fields (shared by all rungs) |
| `pluck/infer.py` | the two model calls (missing fields + category leaf), OpenRouter |
| `pluck/taxonomy.py` | Google taxonomy: top-level list, subtree slices, snap-to-real-path |
| `api.py` | `POST /extract {url}` and `GET /stats`, the deployed front door |
| `fetch.py` | live fetching with honest error reporting |
| `jobq.py` | the queue: leases, backoff, dead letters, deduped enqueue |
| `worker.py` | claim -> fetch -> extract -> store -> discover, forever |
| `seed.py` | seeds the queue from store sitemaps |
| `eval.py` | grades 50 pages against the previous project's verified outputs |

Core shapes:

```python
Field(value=89.40, source="computed")   # source: declared | shipped | computed | inferred | none
Product(name, price, compare_at, currency, category: Field,
        images: list[str],
        meta={"latency_s", "llm_fields", "llm_tokens", "sources"})

# what every rung hands the miner, and what the miner hands back
mine.state(objs, hint) -> {"name": "...", "price": 89.4, "compare_at": 139.0,
                           "currency": "USD", "crumbs": [...]}
```

## Running it

```bash
uv sync                                  # deps
uv run python eval.py                    # 50-page accuracy eval
uv run uvicorn api:app --port 8080       # the api, locally
DATABASE_URL=... python seed.py          # seed the queue
DATABASE_URL=... python worker.py        # a worker
```

`PLUCK_MODEL` picks the model for both calls: `google/gemini-2.5-flash-lite` (default, cheapest) or `google/gemini-3-flash-preview` (category 80% -> 92% at ~6x the LLM cost).
