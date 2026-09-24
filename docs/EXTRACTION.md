# Extraction

## Goal and how it works

Turn one product page's HTML into `{name, price, compare_at, currency, category, images}`, spending as close to nothing as the page allows.

The extractor is a decision tree over four sources, cheapest first. Core fields (name, price, currency) stop the climb as soon as they are filled:

1. **declared** - parse the JSON-LD blocks the merchant wrote for Google. If a block declares several different offer prices, that is an ambiguity, not an answer, and price stays open.
2. **shipped** - parse JSON state embedded as inert script tags (`__NEXT_DATA__`, `application/json`). State blobs hold many products (recommendations, upsells), so candidates are scored against the page title and the best match wins.
3. **computed** - only if core fields are still missing: execute the page's inline JS in a V8 sandbox (stub `window`/`document`, no network, per-script timeouts), snapshot the new globals it built, and mine those. Catches Shopify-style pages that construct state at runtime. Shopify cents (`price: 8940`) are detected by the `handle` key and divided.
4. **inferred** - one small LLM call over the cleaned page text for whatever is missing or disputed.

Two guards keep the deterministic answers honest:

- **the visible-price referee**: a price from rungs 1-3 must appear in the page's visible text within 1%, and two rungs must not disagree; either violation sends price to the model with the page text.
- **taxonomy descent** for category, which is never on the page: the rung-4 call also picks 1 of 21 top-level categories, then a second call picks the exact path from every real path under that branch. `snap()` maps any stray answer to a real taxonomy string (exact, then valid prefix, then nearest leaf).

Example, a Shopify robe page with no JSON-LD offers: rung 1 gives the name, rung 2 finds only the shop currency, rung 3 runs the page's scripts and finds `variants[0].price: 8940` -> 89.40, the referee confirms 89.40 shows on the page, and the model answers only category. Two sub-cent calls total.

## Call trace

```
extract(html)                          climbs the rungs, assembles the product       pluck/extract.py
├─ rungs.scripts(html)                 pulls every inline <script> body              pluck/rungs.py
├─ mine.jsonld(rungs.declared(scr))    reads the merchant's json-ld for google       pluck/mine.py
├─ mine.state(rungs.shipped(scr))      parses embedded json state, scores candidates pluck/mine.py
├─ mine.state(rungs.computed(scr))     runs page js in a v8 sandbox, reads state     pluck/rungs.py
├─ _visible_prices(html)               price must show on the page or model referees pluck/extract.py
├─ _context(f, html)                   name + breadcrumbs + description for the model pluck/extract.py
├─ infer(html, missing, TOPS, known)   one call: missing fields + top-level category pluck/infer.py
└─ _category(guess, known, html)       taxonomy descent for the category             pluck/extract.py
   ├─ pick_leaf(known, html, subtree)  one call: exact path within that branch       pluck/infer.py
   └─ taxonomy.snap(answer)            snaps any stray answer to a real path         pluck/taxonomy.py
```

## Trace inputs and outputs

The same trace on a real page (the Ace Hardware drill), showing what each step receives and returns.

```jsonc
// rungs.scripts(html)
// in: 663KB of raw html
// out: 46 (type, body) pairs, external src= scripts dropped
[["application/ld+json", "{\"@type\": \"Product\", \"name\": \"DeWalt 20V MAX...\", ...}"],
 ["application/json",    "{\"props\": ...}"],
 ["",                    "window.__STATE__ = {...};"]]
```

```jsonc
// mine.jsonld(rungs.declared(scr))
// in: the parsed ld+json objects
// out: the merchant's declared answer
{"name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)",
 "price": 129.0, "currency": "USD",
 "crumbs": ["Tools", "Power Tools", "Cordless Drills"]}
```

```jsonc
// mine.state(rungs.shipped(scr), hint)
// in: 6 embedded json blobs + the page title as hint
// out: nothing new here, name and price already filled so nothing merges
{"crumbs": ["016013301004-Cordless Drills"]}
```

```jsonc
// mine.state(rungs.computed(scr), hint)  -- SKIPPED on this page, core fields are filled
// on a shopify page it returns e.g. {"name": "Dreamweave Waffle Robe...", "price": 89.4}
```

```jsonc
// _visible_prices(html)
// out: every price shown on the rendered page; 129.0 is in it so the declared price is trusted
[129.0, 149.0, 99.0, ...]
```

```jsonc
// _context(f, html)
// out: the identity string the model sees
"DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger) | Tools > Power Tools > Cordless Drills | The DCD771C2 20V MAX..."
```

```jsonc
// infer(html, missing=["compare_at"], TOPS, known)
// in: cleaned page text + the 21 top level categories
// out: the missing fields plus the branch to descend
{"compare_at": null, "category": "Hardware"}
```

```jsonc
// pick_leaf(known, html, subtree("Hardware"))
// in: all 522 real paths under Hardware + the page text
// out: the model's pick, here it echoed the site's breadcrumb which is not a real path
"Hardware > Tools > Power Tools > Cordless Drills"
```

```jsonc
// taxonomy.snap(answer)
// out: the nearest real taxonomy path
"Hardware > Tools > Drills > Handheld Power Drills"
```

```jsonc
// extract(html) final product
{"name":       {"value": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)", "source": "declared"},
 "price":      {"value": 129.0, "source": "declared"},
 "compare_at": {"value": null,  "source": "none"},
 "currency":   {"value": "USD", "source": "declared"},
 "category":   {"value": "Hardware > Tools > Drills > Handheld Power Drills", "source": "inferred"},
 "images":     ["https://..."],
 "meta": {"latency_s": 1.5, "llm_fields": ["compare_at", "category"],
          "llm_tokens": {"total_tokens": 4570, "cost": 0.0005},
          "sources": {"name": "declared", "price": "declared", "compare_at": "none",
                      "currency": "declared", "category": "inferred"}}}
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

```python
Field(value=89.40, source="computed")   # source: declared | shipped | computed | inferred | none
Product(name, price, compare_at, currency, category: Field,
        images: list[str],
        meta={"latency_s", "llm_fields", "llm_tokens", "sources"})

# what every rung hands the miner, and what the miner hands back
mine.state(objs, hint) -> {"name": "...", "price": 89.4, "compare_at": 139.0,
                           "currency": "USD", "crumbs": [...]}
```

Knobs: `PLUCK_MODEL` (flash-lite default, gemini-3-flash for category 80% -> 92%), `PLUCK_EMBED=0` disables the embedding fallback in `snap()` on small machines.
