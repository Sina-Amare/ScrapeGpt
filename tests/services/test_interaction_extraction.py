"""Phase 2 tests: variant-aware extraction orchestration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.job import ExtractionMode
from app.services.interaction_extraction import extract_records_with_variants
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


@pytest.mark.asyncio
async def test_interactive_without_browser_raises_browser_required():
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
    with pytest.raises(InteractionError) as exc:
        await extract_records_with_variants(
            base_html=TABLE_HTML, source_url="u", project=_project(),
            spec=_spec(profile), max_records=100, fetch_variant_htmls=None,
        )
    assert exc.value.code == "INTERACTION_BROWSER_REQUIRED"


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
