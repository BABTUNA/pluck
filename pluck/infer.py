"""The last rung: one cheap generative call for whatever the page's own data
didn't answer, plus the category pick (taxonomy is never on the page).
"""

import json
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("PLUCK_MODEL", "google/gemini-2.5-flash-lite")


def clean_text(html: str, limit: int = 16_000) -> str:
    text = re.sub(r"<(script|style|svg|noscript)[\s\S]*?</\1>|<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)[:limit]


async def infer(html: str, missing: list[str], categories: list[str],
                known_name: str | None) -> tuple[dict, dict]:
    """Ask for `missing` fields plus a category path. Returns (fields, usage)."""
    keys = missing + ["category"]
    rules = ["Reply with a JSON object with exactly these keys: " + str(keys) + ".",
             "Use null when the page does not state a value.",
             "'category': copy the best-fitting path verbatim from this list: "
             + json.dumps(categories)
             + " — but if none of them fits this product, instead write the correct "
             "full Google Shopping taxonomy path yourself, formatted like "
             "'Apparel & Accessories > Jewelry > Watches'."]
    if "compare_at" in keys:
        rules.append("'compare_at' is the crossed-out / 'was' / list price shown "
                     "next to the current price; null if there is no higher original "
                     "price. Never use another product's or brand's price "
                     "('compare to', 'valued at', 'worth').")
    if "price" in keys:
        rules.append("'price' is the number a buyer pays right now for a standard "
                     "one-time purchase (not a subscription or member price), as a "
                     "decimal number (write 42,01 as 42.01).")
    # a category-only call doesn't need the page, just the identity
    content = clean_text(html) if missing else \
        f"Product: {known_name}\n{clean_text(html, 1_200)}"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPEN_ROUTER_API_KEY']}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system",
                     "content": "Extract product fields from the page text. " + " ".join(rules)},
                    {"role": "user", "content": content},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        )
    r.raise_for_status()
    data = r.json()
    try:
        return (json.loads(data["choices"][0]["message"]["content"]),
                data.get("usage", {}))
    except (KeyError, json.JSONDecodeError):
        return {}, data.get("usage", {})
