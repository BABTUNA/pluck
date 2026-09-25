"""a guided tour of every extraction mechanism, one real page each

    uv run python tour.py

each stop prints what the page offered, which mechanism fired, and what came out
"""

import asyncio
import json
from pathlib import Path

from pluck import mine, rungs, taxonomy
from pluck.extract import _hint, _visible_prices, extract

DATA = Path("/Users/benba/projects/interviews/take-home-2026")


def page(stem):
    return next(DATA.glob(f"data*/{stem}.html")).read_text(errors="ignore")


def show(title, body):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}\n{body}")


async def main():
    # -- rung 1: declared. the merchant wrote the answer for google ---------
    scr = rungs.scripts(page("ace"))
    got = mine.jsonld(rungs.declared(scr))
    show("rung 1 declared (ace drill): parse json-ld, done for free",
         json.dumps({k: v for k, v in got.items() if k != "images"}, indent=1)[:400])

    # -- rung 2: shipped. inert json the framework left in the page ---------
    html = page("casper")
    got = mine.state(rungs.shipped(rungs.scripts(html)), _hint(html))
    show("rung 2 shipped (casper): best product candidate out of state blobs",
         json.dumps(got, indent=1)[:300])

    # -- rung 3: computed. run the page's own js in a v8 sandbox ------------
    html = page("brooklinen")
    # the sandbox blocks, so off the event loop, exactly as extract() does it
    globs = await asyncio.to_thread(rungs.computed, rungs.scripts(html))
    got = mine.state(globs, _hint(html))
    show("rung 3 computed (brooklinen): sandbox globals, shopify cents decoded,\n"
         "variant matrix with option labels",
         json.dumps({**got, "variants": got.get("variants", [])[:2]}, indent=1)[:500])

    # -- ambiguity: several declared prices is a dispute, not an answer -----
    got = mine.jsonld(rungs.declared(rungs.scripts(page("dossier"))))
    show("declared ambiguity (dossier): 5 offer prices -> conflict flag, price\n"
         "stays open and the model referees with the visible page",
         json.dumps({"name": got.get("name"), "conflict": got.get("conflict"),
                     "price": got.get("price")}, indent=1))

    # -- the referee: a price must show on the rendered page ----------------
    html = page("peakdesign")
    declared = mine.jsonld(rungs.declared(rungs.scripts(html))).get("price")
    vis = sorted(_visible_prices(html))[:8]
    show("visible-price referee (peakdesign)",
         f"declared price: {declared}\nvisible prices: {vis}\n"
         f"declared not on screen -> disputed -> model reads the page")

    # -- category descent: branch pick then one choice inside real paths ----
    sub = taxonomy.subtree("Hardware")
    show("category descent", f"21 top levels -> model picks 'Hardware' -> "
         f"{len(sub)} real paths under it -> one pick, then snap()\n"
         f"snap('Hardware > Tools > Power Tools > Cordless Drills') -> "
         f"{taxonomy.snap('Hardware > Tools > Power Tools > Cordless Drills')}")

    # -- the whole tree end to end, with per-field provenance ---------------
    r = await extract(page("llbean"))
    show("full run (llbean): sources per field, model variants fallback",
         json.dumps({
             "fields": {k: {"value": getattr(r, k).value, "source": getattr(r, k).source}
                        for k in ("name", "price", "compare_at", "currency", "category", "brand")},
             "variants": [v["name"] for v in r.variants[:4]],
             "images": len(r.images),
             "llm_asked_for": r.meta["llm_fields"],
         }, indent=1)[:800])


asyncio.run(main())
