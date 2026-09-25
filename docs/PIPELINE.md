# Distributed pipeline and deployment

## Goal and how it works

Run the extractor against the live web as a crawler that scales by adding identical machines, survives crashes, and records every failure. Deployed on Fly.io: an `app` process serving the public API, N `worker` machines, and a small Postgres (`pluck-pg`) that is the only shared state.

![Pluck's distributed crawl and serving architecture](distributed-pipeline.drawio.png)

The queue is a Postgres table and workers coordinate only through atomic claims:

- **seed**: `pipeline/seed.py` pulls real product URLs from each store's public sitemap and inserts them. URLs are normalized (host lowercased, query/fragment stripped) and unique, so a page can only ever be one row.
- **claim with a lease**: a worker takes one ready job and stamps `lease_until = now() + 2 min` in a single `FOR UPDATE SKIP LOCKED` update. Two workers can never get the same row. If a worker dies mid-job the lease expires and the row becomes claimable again, nothing is lost.
- **work**: fetch the page, run `extract()`, upsert the product into `results` keyed by url with `processed_at` (re-crawling later = re-enqueue where stale).
- **failure**: `attempts + 1`, retried after 1m, then 4m, then 16m; after 3 attempts the row moves to `dead_letters` with its error, so blocks and 404s are inspectable instead of retried forever.
- **discovery**: same-domain product links found on each page are enqueued (`ON CONFLICT DO NOTHING`, capped per domain), so the seed grows into a frontier on its own.

The lifecycle of one job: seeded from the gymshark sitemap -> claimed by worker `7811` -> fetched in 0.4s -> extracted (price from shipped state) -> result stored -> 12 discovered leggings pages enqueued -> marked done. A worker froze during the real crawl and its leased pages were reclaimed by the others automatically.

The crawl is driven from the live view (or curl): `POST /crawl/start` runs the default stores or one given url, `pause`/`resume` flip a settings flag every worker checks between jobs, and `clear` wipes only the live batch, the assignment collections in `results` have no queue rows and always survive. Guard rails for a public box: the frontier is hard-capped at 10,000 pages in the enqueue sql itself, user urls are rejected when they point anywhere internal (localhost, private ips, metadata endpoints), and the endpoints carry per-ip rate limits. Workers survive database blips too: a 30s command timeout so a dead socket raises instead of hanging forever, and a loop that logs and retries because the lease system already covers the job.

Measured on the demo crawl (311 live pages, 18 stores): 9 pages/min at 1 worker -> 21 at 4 (`fly scale count worker=4`), $0.71 per 1K pages of actual LLM billing, fetch success 77% with every failure dead-lettered by reason. At 50M products the shape holds and the parts grow: partitioned storage, per-domain rate coordination, a headless fetch tier for stores that ship empty HTML.

## Call trace

```
pipeline/worker.py main()                       one process, N concurrent claim loops         worker.py
└─ loop(pool)                          claim -> work -> repeat, backs off when idle  worker.py
   ├─ jobq.claim(pool)                 atomic lease via FOR UPDATE SKIP LOCKED       pipeline/jobq.py
   ├─ fetch(url)                       live GET, browser headers, one retry          pipeline/fetch.py
   ├─ extract(html)                    the whole decision tree (see EXTRACTION.md)   pluck/extract.py
   ├─ jobq.done(...)                   mark done, upsert product into results        pipeline/jobq.py
   ├─ jobq.fail(...)                   backoff 1m/4m/16m, then dead_letters          pipeline/jobq.py
   └─ jobq.enqueue(discover(html))     same-domain product links, deduped, capped    pipeline/jobq.py

pipeline/seed.py main()                         store sitemaps -> first product urls          seed.py
pipeline/api.py  POST /extract                  fetch + extract for one url, logs every call  api.py
pipeline/api.py  GET /stats                     success rate, rung %, latency from the log    api.py
```

## Files and data structures

| file | what it does |
|---|---|
| `pipeline/jobq.py` | schema + the four queue operations: enqueue, claim, done, fail |
| `pipeline/worker.py` | stateless worker: claim -> fetch -> extract -> store -> discover |
| `pipeline/seed.py` | seeds the queue from store sitemaps |
| `pipeline/fetch.py` | live fetching with honest error strings |
| `pipeline/api.py` | the public demo endpoint on Fly |
| `Dockerfile`, `fly.toml` | one image, two process groups (`app`, `worker`) |

```sql
jobs         (url UNIQUE, domain, status queued|leased|done, attempts,
              lease_until, next_retry)
results      (url PK, product jsonb, worker, batch, processed_at)
settings     (k PK, v)              -- paused, max_pages
dead_letters (url PK, error, attempts, died_at)
```

Ops in practice: `fly deploy` ships both processes, `fly scale count worker=N` is the throughput dial, `fly proxy 15432:5432 -a pluck-pg` opens the DB locally for seeding and analysis. Secrets (`OPEN_ROUTER_API_KEY`, `DATABASE_URL`) live in Fly, never in the image.
