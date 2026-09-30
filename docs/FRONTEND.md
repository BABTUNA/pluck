# Frontend

## Goal and how it works

A storefront over the crawl database, the way a shopper would see the extracted data: a catalog grid, a product detail page, and a live view that drives the crawler. React + TypeScript + Vite, ~770 lines, no component library. The built `dist/` is served by the api itself (`pipeline/api.py`'s SPA catch-all), so one Fly process serves both the JSON and the pages.

Three pages:

- **Catalog** - one grid, three collections behind tabs: Original 50 (the assignment set), Original 5, and Live crawl, each a `?batch=` filter on the same `/products` endpoint, plus a client-side name/brand search. Cards layer the hover photo *over* the primary instead of swapping `src`, so a dead second image never blanks the card; a broken primary retries with the hover image before falling back to a brand-letter placeholder.
- **Product page** - gallery with a thumbnail rail (the product video rides as the last slide with a play badge), price with compare-at strikethrough and percent off via `Intl.NumberFormat`, description, key features, colors, and variants regrouped from flat `"Charcoal / Small"` names into one labeled axis per option (`Color`, `Size`, or `Option n` when the page never named them). Below that, the extracted-data table: every field's value with a small tag saying which rung answered it.
- **Live view** - the crawl's cockpit: run the default stores or paste one URL, cap the frontier with max pages, pause/resume the fleet, clear the live batch (the assignment collections survive by design). It polls `/progress` and `/products?batch=live` every 2.5s, so new products fade in as workers finish them and the bar tracks the frontier, flipping to "Crawl complete" or "Paused" as the queue state changes.

Defensive habits throughout, because every URL came from the wild: `SafeImage` renders a quiet placeholder instead of a browser error glyph and reports broken thumbs so the gallery rail drops them, and the colors section hides itself when the variant axes already show a Color row.

## Component trace

```
App.tsx                                router + the Catalog / Live extraction tabs   src/App.tsx
│
├─ CatalogPage                         batch tabs (fifty | five | live) + search,    src/pages/CatalogPage.tsx
│  │                                   refetches /products?batch= on tab switch
│  │
│  └─ ProductCard                      hover photo layered over the primary, sale    src/components/ProductCard.tsx
│                                      badge, broken-image retry chain
│
├─ ProductPage                         one product by id from /products/{id}         src/pages/ProductPage.tsx
│  │
│  ├─ Gallery                          thumb rail + main pane, video as the last     src/components/Gallery.tsx
│  │                                   slide, broken thumbs drop from the rail
│  │
│  ├─ PriceBlock                       Intl currency formatting, strikethrough +     src/components/PriceBlock.tsx
│  │                                   percent off when compare-at beats price
│  │
│  ├─ groupVariants()                  splits "Charcoal / Small" names on " / ",     src/pages/ProductPage.tsx
│  │                                   dedupes per position, labels axes from
│  │                                   options or falls back to Option n
│  │
│  └─ extracted-data table             field, value, and a source tag per rung       src/pages/ProductPage.tsx
│
└─ LivePage                            polls /progress + /products?batch=live every  src/pages/LivePage.tsx
   │                                   2.5s, new arrivals fade in
   │
   ├─ crawl controls                   crawlStart(url?, maxPages?) / crawlPause /    src/api.ts
   │                                   crawlResume / crawlClear with confirm dialog
   │
   └─ progress bar                     done/total width, flips to Paused or          src/pages/LivePage.tsx
                                       Crawl complete from the queue counts
```

## Files and data structures

| file | what it does |
|---|---|
| `src/App.tsx` | router and the two-tab header |
| `src/pages/CatalogPage.tsx` | the grid, batch tabs, search filter |
| `src/pages/ProductPage.tsx` | PDP: gallery, price, features, colors, variant axes, provenance table |
| `src/pages/LivePage.tsx` | crawl controls, progress bar, arriving-products grid |
| `src/components/ProductCard.tsx` | catalog card with hover layer and sale badge |
| `src/components/Gallery.tsx` | thumb rail + main pane + video slide |
| `src/components/SafeImage.tsx` | wild-URL images degrade to a brand-letter placeholder |
| `src/components/PriceBlock.tsx` | currency formatting and percent-off |
| `src/api.ts` | typed fetch wrappers for every endpoint |
| `src/types.ts` | mirrors the api responses field for field |
| `src/lib/format.ts` | `formatPrice` (Intl, unknown codes degrade gracefully), `percentOff` |

Core shapes (from `src/types.ts`, mirroring the api verbatim):

```ts
ProductSummary { id, name, brand, price: Price, image_url, hover_image_url, category, source }
Product        { ...summary fields, url, description, options: string[], variants: Variant[],
                 colors, key_features, video_url, image_urls, sources: Record<field, rung>,
                 latency_s, worker, processed_at }
Variant        { name, price, compare_at, available? }
Progress       { paused?, done, queued, leased, dead, total }
```

Build and serve:

```bash
cd frontend && npm install && npm run build   # emits dist/, which pipeline/api.py serves
```
