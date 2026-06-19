"""Manual real-URL check: variant capture stays correct on CHILD pages under
concurrent crawling.

Regression for the project-189 bug. The COLLECTION crawl applies the seed's
variant spec (per-100g / per-serving toggle) to every sibling /food/* page. Under
concurrency, competing headless-browser launches crashed the drivers and a
browser-only value silently fell back to the page's STATIC default — so the
per-serving SERVING SIZE stayed "100 g" on child pages (e.g. /food/meat) while
the calories were correct. ``BROWSER_INTERACTION_CONCURRENCY`` now serializes the
captures so the camoufox->Chromium cascade recovers reliably.

This drives several /food/* pages CONCURRENTLY (as the crawl does) and asserts
each captures the real browser-rendered per-serving serving size.

    python -m tests.manual.verify_collection_variant_concurrency

Not part of the pytest suite (it hits the network and a real browser).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.models.job import ExtractionMode
from app.services.fetcher import apply_interactions_and_capture, fetch_url
from app.services.interaction_detect import detect_interaction_profile
from app.services.interaction_extraction import extract_records_with_variants

configure_logging()
logger = logging.getLogger("verify.collection_variant")

ROW = "table.MuiTable-root tr"
SEED = "https://www.calories.info/food/beef-veal"

# (label, url, food, expected serving-size substring, expected per-serving calories)
PAGES = [
    ("meat:Beef", "https://www.calories.info/food/meat", "Beef", "portion", "265"),
    ("meat:Chicken", "https://www.calories.info/food/meat", "Chicken", "piece", "764"),
    ("beef-veal:Beef", SEED, "Beef", "portion", "265"),
    ("poultry:Chicken",
     "https://www.calories.info/food/poultry-chicken-turkey", "Chicken", "piece", "764"),
]


def _of(label: str, sel: str, t: str = "string") -> dict:
    return {"name": label, "label": label, "user_label": label,
            "selector": sel, "type": t, "selected": True}


SEED_FIELDS = [
    _of("Food", "td:nth-child(1) p"),
    _of("Serving Size (per 100 g)", "td:nth-child(2) p"),
    _of("Calories (per 100 g)", "td:nth-child(3)", "number"),
    _of("Serving Size (alternate column)", "td:nth-child(4) p"),
    _of("Calories (alternate column)", "td:nth-child(5)", "number"),
]


async def _run_one(name, url, food, want_serv, want_cal, fields, prof, skey) -> bool:
    fetched = await fetch_url(url, "AUTO")

    async def _cb(recipes):
        return await apply_interactions_and_capture(fetched.final_url, recipes)

    recs, _w = await extract_records_with_variants(
        base_html=fetched.html, source_url=fetched.final_url,
        project=SimpleNamespace(analysis={"repeated_item_selector": ROW}),
        spec=SimpleNamespace(mode=ExtractionMode.STRUCTURED, content_config={},
                             fields=fields, interaction_profile={**prof, "enabled": True}),
        max_records=2000, fetch_variant_htmls=_cb,
    )
    got = {
        (d.get("Food"), str(d.get("serving_basis"))): (d.get(skey), d.get("Calories"))
        for d in (r.normalized_data for r in recs)
    }
    serv, cal = got.get((food, "Show per serving"), (None, None))
    ok = bool(serv and want_serv in str(serv).lower() and str(cal) == want_cal)
    logger.info("[%s] per-serving -> serving=%r cal=%r  %s",
                name, serv, cal, "PASS" if ok else "FAIL")
    return ok


async def main() -> int:
    logger.info(
        "BROWSER_INTERACTION_CONCURRENCY=%s CRAWL_CONCURRENCY=%s",
        settings.BROWSER_INTERACTION_CONCURRENCY, settings.CRAWL_CONCURRENCY,
    )
    seed = await fetch_url(SEED, "AUTO")
    prof, fields = detect_interaction_profile(
        seed.html, SEED_FIELDS, repeated_item_selector=ROW
    )
    fields = fields or SEED_FIELDS
    skey = next((f.get("user_label") or f.get("label") or f.get("name")
                 for f in fields if "serving" in str(f.get("label", "")).lower()),
                "Serving Size")

    # Drive every page CONCURRENTLY — this is what crashed competing browsers.
    results = await asyncio.gather(*[
        _run_one(n, u, food, ss, wc, fields, prof, skey)
        for (n, u, food, ss, wc) in PAGES
    ])
    fails = results.count(False)
    logger.info("CONCURRENT RESULT %s (%d/%d ok)",
                "PASS" if fails == 0 else "FAIL", results.count(True), len(results))
    return fails


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
