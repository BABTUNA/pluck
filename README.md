# pluck

Extract product data (name, price, compare-at, currency, category, images) from any product page HTML. One decision tree: the page's own data answers first, a model only speaks when the page can't.

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

## Files and data structures

| file | what it does |
|---|---|
| `pluck/extract.py` | the router: climbs rungs, detects conflicts, assembles the `Product` |
| `pluck/rungs.py` | the three deterministic harvesters, all returning parsed JSON objects |
| `pluck/mine.py` | one miner that walks any JSON for product fields (shared by all rungs) |
| `pluck/infer.py` | the two model calls (missing fields + category leaf), OpenRouter |
| `pluck/taxonomy.py` | Google taxonomy: top-level list, subtree slices, snap-to-real-path |
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

`PLUCK_MODEL` picks the model for both calls: `google/gemini-2.5-flash-lite` (default, ~$1.20 per 1K pages) or `google/gemini-3-flash-preview` (~$6 per 1K pages, category 82% -> 92%).
