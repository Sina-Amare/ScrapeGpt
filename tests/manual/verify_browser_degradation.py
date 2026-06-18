"""Real-URL proof of the general robustness fix: when the browser is
unavailable/crashes, variant extraction degrades to the page's STATIC data
(per the deterministic column selectors) and warns, instead of hard-failing.

General (not site-specific): exercises the graceful-degradation path on a real
parallel-column page. Run manually (needs network):

    venv\\Scripts\\python.exe -m tests.manual.verify_browser_degradation
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from app.core.logging_config import configure_logging
from app.models.job import ExtractionMode
from app.services.fetcher import fetch_url
from app.services.interaction_detect import detect_interaction_profile
from app.services.interaction_extraction import extract_records_with_variants

configure_logging()
logger = logging.getLogger(__name__)

URL = "https://www.calories.info/food/beef-veal"
ROW = "tr.MuiTableRow-root"

# The analyzer's actual flat fields for this page (inconsistent labels included).
FIELDS = [
    {"name": "Food Name", "label": "Food Name", "user_label": "Food Name",
     "selector": "td.MuiTableCell-body a p", "type": "string", "selected": True},
    {"name": "Serving Size (per 100 g)", "label": "Serving Size (per 100 g)",
     "user_label": "Serving Size (per 100 g)",
     "selector": "td.MuiTableCell-body:nth-child(2)", "type": "string", "selected": True},
    {"name": "Calories (per 100 g)", "label": "Calories (per 100 g)",
     "user_label": "Calories (per 100 g)",
     "selector": "td.MuiTableCell-body:nth-child(3)", "type": "number", "selected": True},
    {"name": "Serving Size (alternate column)", "label": "Serving Size (alternate column)",
     "user_label": "Serving Size (alternate column)",
     "selector": "td.MuiTableCell-body:nth-child(4)", "type": "string", "selected": True},
    {"name": "Calories (alternate column)", "label": "Calories (alternate column)",
     "user_label": "Calories (alternate column)",
     "selector": "td.MuiTableCell-body:nth-child(5)", "type": "number", "selected": True},
]


async def main() -> int:
    fetched = await fetch_url(URL, "AUTO")
    profile, new_fields = detect_interaction_profile(
        fetched.html, FIELDS, repeated_item_selector=ROW
    )
    groups = [(g["metadata_key"], g["execution"]) for g in profile["groups"]]
    logger.info("detected groups=%s collapsed=%s", groups, new_fields is not None)

    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=new_fields or FIELDS,
        interaction_profile={**profile, "enabled": True},
    )

    # Simulate a browser that ALWAYS crashes (camoufox + Chromium both down).
    async def crashing_browser(_recipes):
        raise RuntimeError("Connection closed while reading from the driver")

    recs, warnings = await extract_records_with_variants(
        base_html=fetched.html, source_url=fetched.final_url,
        project=SimpleNamespace(analysis={"repeated_item_selector": ROW}),
        spec=spec, max_records=2000, fetch_variant_htmls=crashing_browser,
    )

    skey = next(
        (f.get("user_label") or f.get("label") or f.get("name")
         for f in (new_fields or FIELDS)
         if "serving" in str(f.get("label", "")).lower()),
        "Serving Size",
    )
    beef = {}
    for r in recs:
        d = r.normalized_data
        if str(d.get("Food Name") or d.get("Food") or "").strip() == "Beef":
            beef[str(d.get("serving_basis"))] = d.get("Calories")
    logger.info("Beef calories by serving_basis (browser DOWN): %s", beef)

    failures = 0
    if not recs:
        logger.error("DEGRADATION FAILED: zero records when browser is down")
        failures += 1
    # The two static calorie columns must BOTH come through despite no browser.
    cal_values = set(beef.values())
    if not ({156} <= cal_values and any(v in (265,) for v in cal_values)):
        logger.error("expected both static calories (156 and 265); got %s", beef)
        failures += 1
    if not any("static values" in w for w in warnings):
        logger.error("expected a degradation warning; got %s", warnings)
        failures += 1
    else:
        logger.info("OK degradation warning present")

    logger.info("records=%s warnings=%s", len(recs), warnings)
    logger.info("verify_browser_degradation done failures=%s", failures)
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
