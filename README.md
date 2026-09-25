# pluck

Extract product data (name, price, compare-at, currency, category, brand, description, variants, images) from any product page. One decision tree: the page's own data answers first, a model only speaks when the page can't.

It is deployed. Try any product URL:

```bash
curl -X POST https://pluck-extract.fly.dev/extract \
  -H 'content-type: application/json' \
  -d '{"url": "https://www.brooklinen.com/products/luxe-core-sheet-set"}'
```

Or browse the storefront it feeds: [pluck-extract.fly.dev](https://pluck-extract.fly.dev) has the assignment's 50 pages, the original 5, a live-crawled catalog, and a live view where you can run, pause, cap, or clear the crawl and feed it single urls.

Deep dives: [EXTRACTION.md](docs/EXTRACTION.md) for the decision tree, [PIPELINE.md](docs/PIPELINE.md) for the distributed crawler and deployment.

## Goal and how it works

Most product pages already contain the answer in machine-readable form. Pluck climbs four rungs, cheapest first, and stops as soon as the core fields (name, price, currency) are filled:

1. **declared** - JSON-LD blocks the merchant wrote for Google. Free, exact.
2. **shipped** - JSON state frameworks embed as inert script tags (`__NEXT_DATA__` and friends). Free.
3. **computed** - run the page's own inline JS in a V8 sandbox and read the state it builds. Free, catches Shopify-style pages that build state at runtime.
4. **inferred** - one small LLM call over the cleaned page text for whatever is still missing.

![Pluck's four-rung extraction decision tree](docs/extraction.drawio.png)

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
| category | 82% | 92% | 96% |
| cost per 1K pages | ~$1.30 | ~$6 | $20 |
| code | ~880 lines | same | ~2,400 lines |

Measured in production (311 live pages, 18 stores, one crawl):

- **$0.71 per 1K pages** (actual OpenRouter billing), latency p50 1.5s / p95 2.5s
- name resolved free on 100% of pages, currency 83%, price 72%; category always uses the model by design
- 77% fetch success; failures are bot-walled stores, each dead-lettered with its reason

## Benchmarks

Ablations over the 50 verified pages (flash-lite, correct counts out of 50):

| config | name | price | compare-at | currency | category | llm tokens |
|---|---|---|---|---|---|---|
| full decision tree | 48 | 48 | 45 | 50 | 41 | 569K |
| without the v8 sandbox rung | 47 | 48 | 45 | 50 | 41 | 569K |
| without the visible-price referee | 48 | 46 | 45 | 50 | 40 | 581K |
| naive: one llm call, no rungs | 42 | 42 | 47 | 43 | 25 | 129K |

The naive baseline is 4x cheaper in tokens and loses everywhere that matters. The sandbox rung barely moves eval accuracy (the model fallback catches those pages) but on the live crawl it answers name and price for free on the pages that ship code instead of data.

Live throughput (same code, more machines):

| workers | pages/min |
|---|---|
| 1 | 9 |
| 4 | 21 |
| 8 | 57 peak, tailing as the per-domain frontier empties |

Live accuracy spot check (30 random crawled products, judged by gemini-3-flash against freshly fetched pages): name 100%, price 87%, compare-at 100%, currency 100%, category 90%. The price misses cluster on one store that ran a sale between crawl and check.

Scaling theory from the measured numbers (one worker sustains ~390K pages/month at $5.70/mo):

| scale | workers | infra | llm (flash-lite) |
|---|---|---|---|
| 1M pages/mo | 3 | ~$40/mo | ~$710/mo |
| 50M pages/mo | ~130 | ~$800/mo | ~$35K/mo |

LLM spend dominates at scale, which is the argument for the free rungs: every field they answer is model spend that never happens.

## Call trace

```
extract(html)                          climbs the rungs, assembles the product       pluck/extract.py
├─ rungs.scripts(html)                 pulls every inline <script> body              pluck/rungs.py
├─ mine.jsonld(rungs.declared(scr))    json-ld: name, price, currency, brand,        pluck/mine.py
│                                      description, images, crumbs, named-offer
│                                      variants; several offer prices = dispute
├─ mine.state(rungs.shipped(scr))      embedded state: best product candidate with   pluck/mine.py
│                                      prices, variant matrix + option labels,
│                                      image arrays and imageUrl keys
├─ mine.state(rungs.computed(scr))     same miner over globals a v8 sandbox built    pluck/rungs.py
│                                      by running the page's own js (shopify cents)
├─ image fallbacks (inline)            while under 2 images: preload links, og,      pluck/extract.py
│                                      then <img> tags at largest srcset rendition
├─ _visible_prices(html)               price must show on the page or model referees pluck/extract.py
├─ _context(f, html)                   name + breadcrumbs + description for the model pluck/extract.py
├─ infer(html, missing, TOPS, known)   one call: missing fields + top-level category pluck/infer.py
├─ _category(guess, known, html)       taxonomy descent for the category             pluck/extract.py
│  ├─ pick_leaf(known, html, subtree)  one call: exact path within that branch       pluck/infer.py
│  └─ taxonomy.snap(answer)            snaps any stray answer to a real path         pluck/taxonomy.py
└─ list_variants(known, html)          tiny call when the rungs found no variants,   pluck/infer.py
                                       runs beside the leaf pick, separate prompt
                                       because sharing one hurt category accuracy
```

## The distributed pipeline

The deployed system is a crawler x extractor with real big-data mechanics, run at demo scale:

![Pluck's distributed crawl and serving architecture](docs/distributed-pipeline.drawio.png)

- Workers coordinate only through atomic claims (`FOR UPDATE SKIP LOCKED`); duplicates are impossible by construction (`url` is unique, inserts are `ON CONFLICT DO NOTHING` on normalized urls).
- Measured scaling: 9 pages/min at 1 worker, 21 pages/min at 4, changed with one command (`fly scale count worker=4`); the deployment runs 4. A worker did freeze mid-crawl once; its leased pages were reclaimed automatically and nothing was lost.
- The live view drives it all: rerun the default stores or a single url, pause and resume the fleet, cap the frontier with max pages, and clear the live catalog (the assignment batches always survive).
- At 50M products the shape stays the same and the parts grow: partitioned job/result storage, per-domain rate-limit coordination, a headless-browser fetch tier for the stores that ship empty HTML, and re-crawl scheduling off the `processed_at` column that already exists.

## Files and data structures

| file | what it does |
|---|---|
| `pluck/extract.py` | the router: climbs rungs, detects conflicts, assembles the `Product` |
| `pluck/rungs.py` | the three deterministic harvesters, all returning parsed JSON objects |
| `pluck/mine.py` | one miner that walks any JSON for product fields (shared by all rungs) |
| `pluck/infer.py` | the three small model calls: fields, category leaf, variants fallback |
| `pluck/taxonomy.py` | Google taxonomy: top-level list, subtree slices, snap-to-real-path |
| `pipeline/api.py` | `POST /extract`, the catalog endpoints, and the crawl controls |
| `pipeline/fetch.py` | live fetching with honest error reporting |
| `pipeline/jobq.py` | the queue: leases, backoff, dead letters, deduped enqueue |
| `pipeline/worker.py` | claim -> fetch -> extract -> store -> discover, forever |
| `pipeline/seed.py` | seeds the queue from store sitemaps |
| `frontend/` | the storefront: catalog tabs, PDPs with provenance, the live crawl view |
| `eval.py` | grades 50 pages against the previous project's verified outputs |

Core shapes:

```python
Field(value=89.40, source="computed")   # source: declared | shipped | computed | inferred | none
Product(name, price, compare_at, currency, category, brand: Field,
        description: str | None,
        options: list[str],     # variant axis labels, like Color and Size
        variants: list[dict],   # {name, price, compare_at, available}
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
uv run uvicorn pipeline.api:app --port 8080       # the api, locally
DATABASE_URL=... python -m pipeline.seed          # seed the queue
DATABASE_URL=... python -m pipeline.worker        # a worker
cd frontend && npm install && npm run build       # the storefront the api serves
```

`PLUCK_MODEL` picks the model for all calls: `google/gemini-2.5-flash-lite` (default, cheapest) or `google/gemini-3-flash-preview` (category 80% -> 92% at ~6x the LLM cost).
