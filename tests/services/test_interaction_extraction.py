"""Phase 2 tests: variant-aware extraction orchestration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.job import ExtractionMode
from app.services.interaction_extraction import (
    VARIANT_QUALITY_COLUMN,
    extract_records_with_variants,
)
from app.services.interaction_profile import InteractionError

# A calories-style table: Food | amount | per-100g cal | amount | per-serving cal
TABLE_HTML = """
<table>
  <tr><td>Beef</td><td>100 g</td><td>156</td><td>1 serving</td><td>265</td></tr>
  <tr><td>Pork</td><td>100 g</td><td>242</td><td>1 serving</td><td>411</td></tr>
</table>
"""


def _project():
    return SimpleNamespace(analysis={"repeated_item_selector": "tr"})


def _spec(profile):
    return SimpleNamespace(
        mode=ExtractionMode.STRUCTURED,
        content_config={},
        interaction_profile=profile,
        fields=[
            {"name": "Food", "selector": "td:nth-of-type(1)", "type": "string", "selected": True},
            {"name": "Calories", "selector": "td:nth-of-type(3)", "type": "number", "selected": True},
        ],
    )


def _deterministic_profile():
    return {
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
    }


@pytest.mark.asyncio
async def test_disabled_profile_is_passthrough_without_metadata():
    spec = _spec({})
    records, warnings = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="u", project=_project(),
        spec=spec, max_records=100,
    )
    assert len(records) == 2
    assert "interaction_variant_id" not in records[0].normalized_data
    assert warnings == []


@pytest.mark.asyncio
async def test_deterministic_variants_read_alternate_columns_no_browser():
    spec = _spec(_deterministic_profile())
    records, warnings = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="u", project=_project(),
        spec=spec, max_records=100,
        fetch_variant_htmls=None,  # deterministic -> never needs a browser
    )
    # 2 rows x 2 variants.
    assert len(records) == 4
    by_variant: dict[str, list] = {}
    for r in records:
        by_variant.setdefault(r.normalized_data["serving_basis"], []).append(
            r.normalized_data["Calories"]
        )
    assert sorted(by_variant["per 100 g"]) == [156, 242]
    assert sorted(by_variant["per serving"]) == [265, 411]
    assert warnings == []


@pytest.mark.asyncio
async def test_interactive_variant_uses_browser_html():
    profile = {
        "enabled": True,
        "max_variant_combinations": 12,
        "groups": [
            {
                "label": "Unit system",
                "metadata_key": "unit_system",
                "execution": "interactive",
                "options": [
                    {"id": "metric", "label": "Metric", "selected": True, "recipe": []},
                    {"id": "imperial", "label": "Imperial", "selected": True,
                     "recipe": [{"action": "click", "by": "text", "value": "Imperial"}]},
                ],
            }
        ],
    }
    imperial_html = """
    <table>
      <tr><td>Beef</td><td>3.5 oz</td><td>156</td></tr>
      <tr><td>Pork</td><td>3.5 oz</td><td>242</td></tr>
    </table>
    """
    captured: dict = {}

    async def fake_fetch(recipes):
        captured["recipes"] = recipes
        return {cid: imperial_html for cid in recipes}

    spec = _spec(profile)
    records, _ = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="u", project=_project(),
        spec=spec, max_records=100, fetch_variant_htmls=fake_fetch,
    )
    # Only the imperial combo required a browser snapshot.
    assert list(captured["recipes"].keys()) == ["imperial"]
    units = {r.normalized_data["unit_system"] for r in records}
    assert units == {"Metric", "Imperial"}


def _mixed_profile():
    """calories.info shape: static per-variant columns AND a browser toggle."""
    return {
        "enabled": True,
        "max_variant_combinations": 12,
        "groups": [
            {
                "label": "Serving basis",
                "metadata_key": "serving_basis",
                "execution": "mixed",
                "options": [
                    {"id": "per_100g", "label": "Show per 100 g", "selected": True,
                     "field_selectors": {"Calories": "td:nth-of-type(3)"},
                     "recipe": []},
                    {"id": "per_serving", "label": "Show per serving",
                     "selected": True,
                     "field_selectors": {"Calories": "td:nth-of-type(5)"},
                     "recipe": [{"action": "click", "by": "text",
                                 "value": "Show per serving"}]},
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_interactive_without_browser_degrades_not_fails():
    """A pure-interactive variant with no browser must NOT sink the extraction.
    The no-browser baseline still extracts; the browser-only option (no static
    columns to fall back to) is skipped with a warning instead of a hard fail."""
    profile = {
        "enabled": True,
        "max_variant_combinations": 12,
        "groups": [
            {
                "label": "Unit system",
                "metadata_key": "unit_system",
                "execution": "interactive",
                "options": [
                    {"id": "metric", "label": "Metric", "selected": True, "recipe": []},
                    {"id": "imperial", "label": "Imperial", "selected": True,
                     "recipe": [{"action": "click", "by": "text", "value": "Imperial"}]},
                ],
            }
        ],
    }
    records, warnings = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="u", project=_project(),
        spec=_spec(profile), max_records=100, fetch_variant_htmls=None,
    )
    # Baseline survives (real static data); the browser-only variant is skipped.
    assert {r.normalized_data["unit_system"] for r in records} == {"Metric"}
    assert any("Imperial" in w and "browser" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_mixed_variant_degrades_to_static_columns_on_browser_crash():
    """Headline robustness case: a 'mixed' page (static columns + browser toggle)
    whose browser CRASHES must still deliver the static per-variant data (both
    calorie columns) plus a visible warning — never a hard failure.

    AND the degraded (browser-required) rows must be stamped with a visible
    ``data_quality`` flag so a value that fell back to the page's static default
    can never silently look clean in the export/UI, while cleanly-extracted rows
    stay unflagged."""

    async def boom(_recipes):
        raise RuntimeError("Connection closed while reading from the driver")

    records, warnings = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="u", project=_project(),
        spec=_spec(_mixed_profile()), max_records=100, fetch_variant_htmls=boom,
    )
    by_variant: dict[str, list] = {}
    for r in records:
        by_variant.setdefault(r.normalized_data["serving_basis"], []).append(
            r.normalized_data["Calories"]
        )
    # The per-serving variant read its STATIC column even though the browser died.
    assert sorted(by_variant["Show per 100 g"]) == [156, 242]
    assert sorted(by_variant["Show per serving"]) == [265, 411]
    assert any("static values" in w for w in warnings)

    # Loud-degrade: browser-required rows are flagged; the static-only rows are not.
    per_serving = [r for r in records
                   if r.normalized_data["serving_basis"] == "Show per serving"]
    per_100g = [r for r in records
                if r.normalized_data["serving_basis"] == "Show per 100 g"]
    assert per_serving and all(
        "stale" in str(r.normalized_data.get(VARIANT_QUALITY_COLUMN, "")).lower()
        for r in per_serving
    ), "degraded per-serving rows must carry the data_quality flag"
    assert all(
        VARIANT_QUALITY_COLUMN in r.normalized_data for r in per_serving
    )
    assert all(
        VARIANT_QUALITY_COLUMN not in r.normalized_data for r in per_100g
    ), "cleanly-extracted static rows must NOT be flagged"
    # The flag is also a row-level warning.
    assert all(
        any("stale" in w.lower() for w in r.warnings) for r in per_serving
    )


@pytest.mark.asyncio
async def test_partial_zero_variant_is_a_warning_not_a_failure():
    # Interactive group: Metric baseline (base_html has rows), Imperial combo's
    # browser snapshot has no matching table -> zero records for that variant.
    profile = {
        "enabled": True,
        "max_variant_combinations": 12,
        "groups": [
            {
                "label": "Unit system",
                "metadata_key": "unit_system",
                "execution": "interactive",
                "options": [
                    {"id": "metric", "label": "Metric", "selected": True, "recipe": []},
                    {"id": "imperial", "label": "Imperial", "selected": True,
                     "recipe": [{"action": "click", "by": "text", "value": "Imperial"}]},
                ],
            }
        ],
    }

    async def fake_fetch(recipes):
        return {cid: "<html><body><p>no table here</p></body></html>" for cid in recipes}

    records, warnings = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="u", project=_project(),
        spec=_spec(profile), max_records=100, fetch_variant_htmls=fake_fetch,
    )
    # Metric produced rows; Imperial produced none -> warning, not error.
    assert any(r.normalized_data["unit_system"] == "Metric" for r in records)
    assert warnings and "Imperial" in warnings[0]


@pytest.mark.asyncio
async def test_variant_cap_exceeded_raises():
    profile = _deterministic_profile()
    profile["max_variant_combinations"] = 1
    with pytest.raises(InteractionError) as exc:
        await extract_records_with_variants(
            base_html=TABLE_HTML, source_url="u", project=_project(),
            spec=_spec(profile), max_records=100,
        )
    assert exc.value.code == "INTERACTION_VARIANT_LIMIT_EXCEEDED"
