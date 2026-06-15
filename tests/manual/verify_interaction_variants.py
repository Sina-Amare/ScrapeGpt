"""Manual real-URL verification for Phase 2 deterministic page variants.

Proves the headline case end-to-end without a browser: calories.info shows both
"per 100 g" and "per serving" calories as parallel columns in the static DOM, so
a deterministic interaction_profile extracts BOTH as separate, labelled rows from
a single fetch. Run manually (needs network):

    python -m tests.manual.verify_interaction_variants

Not part of the pytest suite (it hits the network).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from app.core.logging_config import configure_logging
from app.models.job import ExtractionMode
from app.services.fetcher import fetch_url
from app.services.interaction_detect import detect_interaction_groups
from app.services.interaction_extraction import extract_records_with_variants

configure_logging()
logger = logging.getLogger(__name__)

URL = "https://www.calories.info/food/beef-veal"
ROW = "table.MuiTable-root tr"


def _spec():
    return SimpleNamespace(
        mode=ExtractionMode.STRUCTURED,
        content_config={},
        fields=[
            {"name": "Food", "selector": "td:nth-of-type(1)", "type": "string", "selected": True},
            {"name": "Calories", "selector": "td:nth-of-type(3)", "type": "number", "selected": True},
        ],
        interaction_profile={
            "enabled": True,
            "max_variant_combinations": 12,
            "groups": [
                {
                    "label": "Serving basis",
                    "metadata_key": "serving_basis",
                    "execution": "deterministic",
                    "options": [
                        {"id": "per_100g", "label": "per 100 g", "selected": True,
                         "field_selectors": {"Calories": "td:nth-of-type(3)"}},
                        {"id": "per_serving", "label": "per serving", "selected": True,
                         "field_selectors": {"Calories": "td:nth-of-type(5)"}},
                    ],
                }
            ],
        },
    )


async def main() -> int:
    fetched = await fetch_url(URL, "AUTO")
    project = SimpleNamespace(analysis={"repeated_item_selector": ROW})

    records, warnings = await extract_records_with_variants(
        base_html=fetched.html,
        source_url=fetched.final_url,
        project=project,
        spec=_spec(),
        max_records=1000,
        fetch_variant_htmls=None,  # deterministic -> no browser needed
    )

    by_food: dict[str, dict[str, object]] = {}
    for r in records:
        d = r.normalized_data
        by_food.setdefault(str(d.get("Food")), {})[str(d.get("serving_basis"))] = d.get("Calories")

    sample = next(
        (f for f, v in by_food.items()
         if v.get("per 100 g") is not None and v.get("per serving") is not None
         and v["per 100 g"] != v["per serving"]),
        None,
    )
    bases = {str(r.normalized_data.get("serving_basis")) for r in records}

    failures = 0
    if len(records) < 80:  # ~46 rows x 2 variants
        logger.error("too few records: %s", len(records)); failures += 1
    if bases != {"per 100 g", "per serving"}:
        logger.error("unexpected variant labels: %s", bases); failures += 1
    if sample is None:
        logger.error("no food had differing per-100g vs per-serving calories"); failures += 1
    else:
        v = by_food[sample]
        logger.info(
            "OK deterministic variants: %s -> per100g=%s, perServing=%s",
            sample, v["per 100 g"], v["per serving"],
        )
    logger.info("records=%s variants=%s warnings=%s", len(records), bases, warnings)
    logger.info(
        "detected controls on page (informational): %s",
        [g["metadata_key"] for g in detect_interaction_groups(fetched.html)],
    )
    logger.info("verify_interaction_variants done failures=%s", failures)
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
