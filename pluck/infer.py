"""
the last rung where one cheap model call answers what the page didnt
  infer      ask for the missing fields plus a top level category in one json shot
  pick_leaf  run the second half of the category descent inside the chosen branch
  clean_text strip the page down to what a human would read
"""

import json
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("PLUCK_MODEL", "google/gemini-2.5-flash-lite")


# strip the page down to what a human would read
def clean_text(html: str, limit: int = 16_000) -> str:
    text = re.sub(r"<(script|style|svg|noscript)[\s\S]*?</\1>|<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)[:limit]


# ask one json call for the missing or disputed fields plus a top level category
# the rules encode judgment calls like one time price and no other brands compare at
async def infer(html: str, missing: list[str], tops: list[str],
                known_name: str | None, want_variants: bool = False) -> tuple[dict, dict]:
    keys = missing + ["category"]
    if want_variants:
        keys.append("variants")
    rules = ["Reply with a JSON object with exactly these keys: " + str(keys) + ".",
             "Use null when the page does not state a value.",
             "'category': the best-fitting top-level Google Shopping category, "
             "copied verbatim from this list: " + json.dumps(tops)]
    if want_variants:
        rules.append("'variants': the selectable configurations of this product shown "
                     "on the page (sizes, colors, fits) as an array of short strings "
                     "like [\"Black / S\", \"Black / M\"], or [] if there are none.")
    if "currency" in keys:
        rules.append("'currency' is the ISO 4217 code of the displayed prices; infer "
                     "it from the symbol and site (a $ price on a US site is USD).")
    if "compare_at" in keys:
        rules.append("'compare_at' is the crossed-out / 'was' / list price shown "
                     "next to the current price; null if there is no higher original "
                     "price. Never use another product's or brand's price "
                     "('compare to', 'valued at', 'worth').")
    if "price" in keys:
        rules.append("'price' is the number a buyer pays right now for a standard "
                     "one-time purchase (not a subscription or member price), as a "
                     "decimal number (write 42,01 as 42.01).")
    content = clean_text(html) if missing else \
        f"Product: {known_name}\n{clean_text(html, 4_000)}"
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


# run the second half of the category descent with one verbatim pick from the branch
async def pick_leaf(known: str, html: str, paths: list[str]) -> tuple[str | None, dict]:
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPEN_ROUTER_API_KEY']}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content":
                     "Pick the single best-fitting category path for this product; "
                     "prefer a general path over a specific one unless the specific "
                     "clearly applies. Reply JSON {\"category\": \"<one string copied "
                     "verbatim from the list>\"}. List: " + json.dumps(paths)},
                    {"role": "user", "content": f"{known}\n{clean_text(html, 4_000)}"},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        )
    r.raise_for_status()
    data = r.json()
    try:
        return (json.loads(data["choices"][0]["message"]["content"]).get("category"),
                data.get("usage", {}))
    except (KeyError, json.JSONDecodeError):
        return None, data.get("usage", {})
