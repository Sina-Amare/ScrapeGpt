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
from app.services.fetcher import apply_interactions_and_capture, fetch_url
from app.services.interaction_detect import (
    detect_column_variants,
    detect_interaction_groups,
    detect_interaction_profile,
    repair_parallel_column_selectors,
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

    # Selector-repair on REAL HTML — two honest cases:
    #
    # (a) RESTORES a recoverable column. Calories per-100g (156) and per-serving
    #     (265) are BOTH in the static DOM (distinct columns), so when we
    #     simulate the analyzer duplicating "Calories 2" onto "Calories 1"'s
    #     column, repair infers the right column from the sibling spacing and
    #     verifies it against the page.
    def _sel(fields, label):
        return next(f["selector"] for f in fields if f["label"] == label)

    cal_dup = [dict(f) for f in ANALYZER_FIELDS]
    for f in cal_dup:
        if f["label"] == "Calories 2":
            f["selector"] = _sel(cal_dup, "Calories 1")  # break it
    cal_fixed = _sel(
        repair_parallel_column_selectors(cal_dup, fetched.html, repeated_item_selector=ROW),
        "Calories 2",
    )
    cal_expected = _sel(ANALYZER_FIELDS, "Calories 2")
    logger.info("repair RESTORES Calories 2 -> %s (expected %s)", cal_fixed, cal_expected)
    if cal_fixed != cal_expected:
        logger.error("repair failed to restore the distinct Calories column")
        failures += 1

    # (b) DECLINES when the value is not in the static DOM. The per-serving
    #     SERVING SIZE ("1 portion (...)") only appears after the browser toggle;
    #     statically both serving columns read "100 g". Repair must NOT fabricate
    #     a value — it leaves the duplicate in place (the duplicate-column warning
    #     then tells the user, and the serving_basis toggle stays available).
    serv_dup = [dict(f) for f in ANALYZER_FIELDS]
    for f in serv_dup:
        if f["label"] == "Serving Size 2":
            f["selector"] = _sel(serv_dup, "Serving Size 1")  # break it
    serv_after = _sel(
        repair_parallel_column_selectors(serv_dup, fetched.html, repeated_item_selector=ROW),
        "Serving Size 2",
    )
    logger.info(
        "repair DECLINES Serving Size 2 (value not in static DOM) -> stays %s",
        serv_after,
    )
    if serv_after != _sel(serv_dup, "Serving Size 1"):
        logger.error("repair changed a serving column whose value is not static")
        failures += 1

    # --- MERGE + browser E2E on REAL HTML --------------------------------
    # With the labels the analyzer now emits ("(first reported serving)"), the
    # static per-100g/per-serving calorie columns and the interactive serving
    # toggle MERGE into one 'mixed' axis. The per-serving SERVING SIZE
    # ("1 portion (...)") is not in the static DOM, so it comes from the browser
    # toggle (camoufox crashes here -> cascades to Chromium).
    def _of(label, sel, t="string"):
        return {"name": label, "label": label, "user_label": label,
                "selector": sel, "type": t, "selected": True}

    ord_fields = [
        _of("Food", "td:nth-child(1) p"),
        _of("Serving size (first reported serving)", "td:nth-child(2) p"),
        _of("Calories (first reported serving)", "td:nth-child(3)", "number"),
        _of("Serving size (second reported serving)", "td:nth-child(4) p"),
        _of("Calories (second reported serving)", "td:nth-child(5)", "number"),
    ]
    mprof, mfields = detect_interaction_profile(
        fetched.html, ord_fields, repeated_item_selector=ROW
    )
    mkeys = [(g["metadata_key"], g["execution"]) for g in mprof["groups"]]
    logger.info(
        "merge detection: groups=%s fields=%s", mkeys,
        [f["label"] for f in (mfields or ord_fields)],
    )
    if not any(k == "serving_basis" and e == "mixed" for k, e in mkeys):
        logger.error("merge did not produce a mixed serving_basis group")
        failures += 1
    else:
        async def _cb(recipes):
            return await apply_interactions_and_capture(fetched.final_url, recipes)

        mspec = SimpleNamespace(
            mode=ExtractionMode.STRUCTURED, content_config={}, fields=mfields,
            interaction_profile={**mprof, "enabled": True},
        )
        mrecs, _mw = await extract_records_with_variants(
            base_html=fetched.html, source_url=fetched.final_url,
            project=SimpleNamespace(analysis={"repeated_item_selector": ROW}),
            spec=mspec, max_records=1000, fetch_variant_htmls=_cb,
        )
        got = {
            (d.get("Food"), str(d.get("serving_basis")), str(d.get("unit_system"))):
            (d.get("Serving size"), d.get("Calories"))
            for d in (r.normalized_data for r in mrecs)
        }
        for combo in [
            ("Beef", "Show per 100 g", "Metric"),
            ("Beef", "Show per serving", "Metric"),
            ("Beef", "Show per serving", "Imperial"),
        ]:
            logger.info("merge E2E %s -> serving=%r cal=%r", combo, *got.get(combo, (None, None)))
        real = got.get(("Beef", "Show per serving", "Metric"), (None, None))[0]
        if not (real and "portion" in str(real).lower()):
            logger.error("merge E2E did not yield the real per-serving serving size")
            failures += 1
        else:
            logger.info("OK merge E2E: real per-serving serving size via browser = %r", real)

    logger.info("verify_interaction_variants done failures=%s", failures)
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
