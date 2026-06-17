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
from app.services.interaction_detect import (
    detect_column_variants,
    detect_interaction_groups,
    detect_interaction_profile,
)
from app.services.interaction_extraction import extract_records_with_variants

configure_logging()
logger = logging.getLogger(__name__)

URL = "https://www.calories.info/food/beef-veal"
ROW = "table.MuiTable-root tr"

# Flat fields exactly as the analyzer models this page (numbered parallel
# columns). The deterministic variant group is then AUTO-detected from these —
# proving the real product workflow (detect -> enable -> extract), not a
# hand-authored profile.
ANALYZER_FIELDS = [
    {"name": "Food", "label": "Food", "user_label": "Food",
     "selector": "td:nth-child(1) p", "type": "string", "selected": True},
    {"name": "Serving Size 1", "label": "Serving Size 1", "user_label": "Serving Size 1",
     "selector": "td:nth-child(2) p", "type": "string", "selected": True},
    {"name": "Calories 1", "label": "Calories 1", "user_label": "Calories 1",
     "selector": "td:nth-child(3) p", "type": "number", "selected": True},
    {"name": "Serving Size 2", "label": "Serving Size 2", "user_label": "Serving Size 2",
     "selector": "td:nth-child(4) p", "type": "string", "selected": True},
    {"name": "Calories 2", "label": "Calories 2", "user_label": "Calories 2",
     "selector": "td:nth-child(5) p", "type": "number", "selected": True},
]


async def main() -> int:
    fetched = await fetch_url(URL, "AUTO")
    project = SimpleNamespace(analysis={"repeated_item_selector": ROW})

    # AUTO-detect the deterministic variant group + collapsed fields, then enable.
    new_fields, group = detect_column_variants(ANALYZER_FIELDS)
    profile = {"enabled": True, "max_variant_combinations": 12, "groups": [group]}
    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=new_fields, interaction_profile=profile,
    )

    records, warnings = await extract_records_with_variants(
        base_html=fetched.html,
        source_url=fetched.final_url,
        project=project,
        spec=spec,
        max_records=1000,
        fetch_variant_htmls=None,  # deterministic -> no browser needed
    )

    by_food: dict[str, dict[str, object]] = {}
    for r in records:
        d = r.normalized_data
        by_food.setdefault(str(d.get("Food")), {})[str(d.get("column_set"))] = d.get("Calories")

    sample = next(
        (f for f, v in by_food.items()
         if v.get("Variant 1") is not None and v.get("Variant 2") is not None
         and v["Variant 1"] != v["Variant 2"]),
        None,
    )
    bases = {str(r.normalized_data.get("column_set")) for r in records}

    failures = 0
    if group is None:
        logger.error("column-variant auto-detection failed"); failures += 1
    if len(records) < 80:  # ~46 rows x 2 variants
        logger.error("too few records: %s", len(records)); failures += 1
    if bases != {"Variant 1", "Variant 2"}:
        logger.error("unexpected variant labels: %s", bases); failures += 1
    if sample is None:
        logger.error("no food had differing variant-1 vs variant-2 calories"); failures += 1
    else:
        v = by_food[sample]
        logger.info(
            "OK auto-detected deterministic variants: %s -> v1=%s, v2=%s",
            sample, v["Variant 1"], v["Variant 2"],
        )
    logger.info("records=%s variants=%s warnings=%s", len(records), bases, warnings)

    # Phase 3: merge mode. beef-veal embeds a duplicate "CTA" row, so the stable
    # key (Food) is non-unique -> merge must CONSERVATIVELY fall back to
    # row-per-variant with a warning (P2b), never pair rows by index and mix
    # entities. (The clean happy-path merge is covered by unit tests.)
    merge_spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=new_fields,
        interaction_profile={**profile, "merge_variants": True},
    )
    merged, merge_warnings = await extract_records_with_variants(
        base_html=fetched.html, source_url=fetched.final_url, project=project,
        spec=merge_spec, max_records=1000, fetch_variant_htmls=None,
    )
    fell_back = any("safely" in w for w in merge_warnings)
    if fell_back and len(merged) == len(records):
        logger.info(
            "OK merge conservative fallback: non-unique key -> row-per-variant + warning"
        )
    else:
        logger.error(
            "merge fallback check failed: rows=%s warnings=%s", len(merged), merge_warnings
        )
        failures += 1

    logger.info(
        "detected controls on page (informational): %s",
        [g["metadata_key"] for g in detect_interaction_groups(fetched.html)],
    )

    # Full product detection path: detect_interaction_profile over the analyzer
    # fields + page HTML. This makes the browser-free SCOPE explicit — only the
    # axes the analyzer columned become deterministic; an un-columned toggle axis
    # (e.g. metric/imperial here) stays INTERACTIVE and would still need a browser.
    full_profile, _collapsed = detect_interaction_profile(
        fetched.html, ANALYZER_FIELDS
    )
    deterministic = [
        g["metadata_key"]
        for g in full_profile["groups"]
        if g["execution"] == "deterministic"
    ]
    still_interactive = [
        g["metadata_key"]
        for g in full_profile["groups"]
        if g["execution"] == "interactive"
    ]
    logger.info(
        "full detect_interaction_profile -> deterministic(browser-free)=%s "
        "interactive(needs browser)=%s",
        deterministic,
        still_interactive,
    )
    if "column_set" not in deterministic:
        logger.error("full profile path lost the deterministic column_set group")
        failures += 1

    logger.info("verify_interaction_variants done failures=%s", failures)
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
