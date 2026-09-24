"""The queue is a Postgres table; workers coordinate only through atomic
claims. Leases make crashes harmless, backoff makes rate limits polite,
the dead-letter table makes failures inspectable.
"""

import os

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id          bigserial PRIMARY KEY,
  url         text UNIQUE NOT NULL,
  domain      text NOT NULL,
  status      text NOT NULL DEFAULT 'queued',   -- queued | leased | done
  attempts    int  NOT NULL DEFAULT 0,
  lease_until timestamptz,
  next_retry  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs (status, next_retry);
CREATE TABLE IF NOT EXISTS results (
  url          text PRIMARY KEY,
  product      jsonb NOT NULL,
  worker       text,
  processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dead_letters (
  url      text PRIMARY KEY,
  error    text,
  attempts int,
  died_at  timestamptz NOT NULL DEFAULT now()
);
"""


def norm(url: str) -> str:
    """same page must mean same string, or dedupe can't work"""
    from urllib.parse import urlsplit
    s = urlsplit(url.strip())
    return f"{s.scheme}://{s.netloc.lower()}{s.path.rstrip('/')}"


async def connect() -> asyncpg.Pool:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    async with pool.acquire() as c:
        await c.execute(SCHEMA)
    return pool


async def enqueue(pool, urls: list[str], per_domain_cap: int = 40) -> int:
    """insert new urls, silently dropping duplicates and full domains"""
    n = 0
    async with pool.acquire() as c:
        for u in {norm(u) for u in urls}:
            domain = u.split("/")[2]
            n += await c.fetchval(
                """INSERT INTO jobs (url, domain)
                   SELECT $1, $2 WHERE
                     (SELECT count(*) FROM jobs WHERE domain = $2) < $3
                   ON CONFLICT (url) DO NOTHING RETURNING 1""",
                u, domain, per_domain_cap) or 0
    return n


async def claim(pool, worker: str) -> asyncpg.Record | None:
    """atomically lease one ready job; expired leases are claimable again"""
    async with pool.acquire() as c:
        return await c.fetchrow(
            """UPDATE jobs SET status='leased', lease_until=now() + interval '2 min'
               WHERE id = (
                 SELECT id FROM jobs
                 WHERE (status='queued' AND next_retry <= now())
                    OR (status='leased' AND lease_until < now())
                 ORDER BY id LIMIT 1
                 FOR UPDATE SKIP LOCKED)
               RETURNING id, url""")


async def done(pool, job_id: int, url: str, product: str, worker: str):
    async with pool.acquire() as c:
        await c.execute("UPDATE jobs SET status='done' WHERE id=$1", job_id)
        await c.execute(
            """INSERT INTO results (url, product, worker) VALUES ($1, $2::jsonb, $3)
               ON CONFLICT (url) DO UPDATE SET product=$2::jsonb, processed_at=now()""",
            url, product, worker)


async def fail(pool, job_id: int, url: str, error: str, max_attempts: int = 3):
    """retry with exponential backoff (1m, 4m, 16m), then dead-letter"""
    async with pool.acquire() as c:
        attempts = await c.fetchval(
            "UPDATE jobs SET attempts = attempts + 1 WHERE id=$1 RETURNING attempts", job_id)
        if attempts >= max_attempts:
            await c.execute("DELETE FROM jobs WHERE id=$1", job_id)
            await c.execute(
                """INSERT INTO dead_letters (url, error, attempts) VALUES ($1, $2, $3)
                   ON CONFLICT (url) DO NOTHING""", url, error, attempts)
        else:
            await c.execute(
                """UPDATE jobs SET status='queued',
                   next_retry = now() + (interval '1 min' * power(4, attempts - 1))
                   WHERE id=$1""", job_id)
