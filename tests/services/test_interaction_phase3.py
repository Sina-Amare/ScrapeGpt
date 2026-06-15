"""Phase 3 tests: template fingerprinting, cross-variant merge, url_param variants."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.job import ExtractionMode
from app.services.crawl_scope import _collection_match, classify_links_for_scope
from app.services.interaction_extraction import (
    build_variant_url,
    extract_records_with_variants,
)
from app.services.interaction_profile import (
    InteractionError,
    selected_combinations,
)


# ---------------------------------------------------------------------------
# 3B — template fingerprinting (segment-aware COLLECTION matching)
# ---------------------------------------------------------------------------


def test_collection_match_is_segment_bounded():
    assert _collection_match("https://x.com/food/meat", "/food/*") is True
    assert _collection_match("https://x.com/food/meat/details", "/food/*") is False
    assert _collection_match("https://x.com/food/", "/food/*") is False
    assert _collection_match("https://x.com/drinks/beer", "/food/*") is False


def test_collection_classifier_excludes_deeper_pages():
    html = """
    <a href="/food/meat">Meat</a>
    <a href="/food/fish">Fish</a>
    <a href="/food/meat/beef-cuts">Beef cuts (deeper)</a>
    """
    scope = {
        "mode": "COLLECTION",
        "status": "USER_CONFIRMED",
        "include_patterns": ["/food/*"],
        "max_depth": 1,
    }
    decisions = classify_links_for_scope(
        html, page_url="https://x.com/food/beef",
        root_url="https://x.com/food/beef", scope=scope, source_depth=0,
    )
    inc = {d.normalized_url for d in decisions if d.decision == "included"}
    assert "https://x.com/food/meat" in inc
    assert "https://x.com/food/fish" in inc
    # The deeper sibling is NOT a same-layout list page.
    assert "https://x.com/food/meat/beef-cuts" not in inc


# ---------------------------------------------------------------------------
# 3A — cross-variant row merge
# ---------------------------------------------------------------------------

TABLE_HTML = """
<table>
  <tr><td>Beef</td><td>100 g</td><td>156</td><td>1 serving</td><td>265</td></tr>
  <tr><td>Pork</td><td>100 g</td><td>242</td><td>1 serving</td><td>411</td></tr>
</table>
"""


def _merge_spec():
    return SimpleNamespace(
        mode=ExtractionMode.STRUCTURED,
        content_config={},
        fields=[
            {"name": "Food", "selector": "td:nth-of-type(1)", "type": "string", "selected": True},
            {"name": "Calories", "selector": "td:nth-of-type(3)", "type": "number", "selected": True},
        ],
        interaction_profile={
            "enabled": True,
            "merge_variants": True,
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


@pytest.mark.asyncio
async def test_merge_produces_one_row_per_entity_with_variant_columns():
    project = SimpleNamespace(analysis={"repeated_item_selector": "tr"})
    records, _ = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="u", project=project,
        spec=_merge_spec(), max_records=100,
    )
    # 2 entities (Beef, Pork), not 4 rows.
    assert len(records) == 2
    row = next(r.normalized_data for r in records if r.normalized_data.get("Food") == "Beef")
    # Stable field once; varying field split per variant.
    assert row["Food"] == "Beef"
    assert "Calories" not in row  # it varies, so it is not a single column
    assert row["Calories (per 100 g)"] == 156
    assert row["Calories (per serving)"] == 265
    # No fixed variant-metadata columns in merge mode.
    assert "interaction_variant_id" not in row


# ---------------------------------------------------------------------------
# 3C — URL-parameter variants
# ---------------------------------------------------------------------------


def test_build_variant_url_applies_and_replaces_params():
    assert build_variant_url("https://x.com/p", {"unit": "imperial"}) == (
        "https://x.com/p?unit=imperial"
    )
    # Replaces an existing same-key param, keeps others.
    out = build_variant_url("https://x.com/p?unit=metric&q=1", {"unit": "imperial"})
    assert "unit=imperial" in out and "q=1" in out and "unit=metric" not in out


def _url_param_profile():
    return {
        "enabled": True,
        "max_variant_combinations": 12,
        "groups": [
            {
                "label": "Unit system",
                "metadata_key": "unit_system",
                "execution": "url_param",
                "options": [
                    {"id": "metric", "label": "Metric", "selected": True, "query": {}},
                    {"id": "imperial", "label": "Imperial", "selected": True,
                     "query": {"unit": "imperial"}},
                ],
            }
        ],
    }


def test_url_param_combo_flags():
    combos = selected_combinations(_url_param_profile())
    metric = next(c for c in combos if c.metadata["unit_system"] == "Metric")
    imperial = next(c for c in combos if c.metadata["unit_system"] == "Imperial")
    assert not metric.requires_url_fetch  # empty query -> uses base html
    assert imperial.requires_url_fetch
    assert imperial.url_params == {"unit": "imperial"}
    assert not imperial.requires_browser


@pytest.mark.asyncio
async def test_url_param_variant_fetches_variant_url():
    project = SimpleNamespace(analysis={"repeated_item_selector": "tr"})
    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED,
        content_config={},
        fields=[
            {"name": "Food", "selector": "td:nth-of-type(1)", "type": "string", "selected": True},
            {"name": "Calories", "selector": "td:nth-of-type(3)", "type": "number", "selected": True},
        ],
        interaction_profile=_url_param_profile(),
    )
    imperial_html = (
        "<table><tr><td>Beef</td><td>3.5 oz</td><td>442</td></tr></table>"
    )
    seen_urls: dict = {}

    async def fake_url_fetch(urls):
        seen_urls.update(urls)
        return {vid: imperial_html for vid in urls}

    records, _ = await extract_records_with_variants(
        base_html=TABLE_HTML, source_url="https://x.com/p", project=project,
        spec=spec, max_records=100, fetch_variant_url_htmls=fake_url_fetch,
    )
    # Imperial combo fetched the param URL; metric used base html.
    assert any("unit=imperial" in u for u in seen_urls.values())
    units = {r.normalized_data["unit_system"] for r in records}
    assert units == {"Metric", "Imperial"}


@pytest.mark.asyncio
async def test_url_param_without_fetcher_raises():
    project = SimpleNamespace(analysis={"repeated_item_selector": "tr"})
    spec = SimpleNamespace(
        mode=ExtractionMode.STRUCTURED, content_config={},
        fields=[{"name": "Food", "selector": "td:nth-of-type(1)", "type": "string", "selected": True}],
        interaction_profile=_url_param_profile(),
    )
    with pytest.raises(InteractionError) as exc:
        await extract_records_with_variants(
            base_html=TABLE_HTML, source_url="https://x.com/p", project=project,
            spec=spec, max_records=100, fetch_variant_url_htmls=None,
        )
    assert exc.value.code == "INTERACTION_FETCH_UNAVAILABLE"
