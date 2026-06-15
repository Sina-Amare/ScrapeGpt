"""Phase 2 unit tests: pure interaction_profile helpers."""

from __future__ import annotations

import pytest

from app.services.interaction_profile import (
    InteractionError,
    META_VARIANT_ID,
    META_VARIANT_LABEL,
    apply_field_overrides,
    is_enabled,
    metadata_columns,
    sanitize_metadata_key,
    selected_combinations,
    tag_record_metadata,
)


def _profile(**over):
    base = {
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
            },
            {
                "label": "Unit system",
                "metadata_key": "unit_system",
                "execution": "interactive",
                "options": [
                    {"id": "metric", "label": "Metric", "selected": True, "recipe": []},
                    {"id": "imperial", "label": "Imperial", "selected": True,
                     "recipe": [{"action": "click", "by": "text", "value": "Imperial"}]},
                ],
            },
        ],
    }
    base.update(over)
    return base


def test_is_enabled():
    assert is_enabled(None) is False
    assert is_enabled({}) is False
    assert is_enabled({"enabled": True, "groups": []}) is False
    assert is_enabled(_profile()) is True
    assert is_enabled(_profile(enabled=False)) is False


def test_sanitize_metadata_key_standard_and_fallback():
    assert sanitize_metadata_key("Metric/Imperial") == "unit_system"
    assert sanitize_metadata_key("per 100 g / per serving") == "serving_basis"
    assert sanitize_metadata_key("Pack Size!!") == "pack_size"
    assert sanitize_metadata_key("") == "variant"


def test_selected_combinations_cartesian_product():
    combos = selected_combinations(_profile())
    assert len(combos) == 4
    labels = {c.label for c in combos}
    assert any("per 100 g" in l and "Metric" in l for l in labels)
    # Metric combos need no browser; Imperial combos do.
    metric = [c for c in combos if c.metadata["unit_system"] == "Metric"]
    imperial = [c for c in combos if c.metadata["unit_system"] == "Imperial"]
    assert all(not c.requires_browser for c in metric)
    assert all(c.requires_browser for c in imperial)


def test_selected_combinations_disabled_returns_empty():
    assert selected_combinations(_profile(enabled=False)) == []
    assert selected_combinations(None) == []


def test_selected_combinations_respects_selection():
    prof = _profile()
    # Deselect imperial -> only metric remains for that group.
    prof["groups"][1]["options"][1]["selected"] = False
    combos = selected_combinations(prof)
    assert len(combos) == 2
    assert all(c.metadata["unit_system"] == "Metric" for c in combos)


def test_selected_combinations_cap_exceeded():
    prof = _profile(max_variant_combinations=2)
    with pytest.raises(InteractionError) as exc:
        selected_combinations(prof)
    assert exc.value.code == "INTERACTION_VARIANT_LIMIT_EXCEEDED"


def test_apply_field_overrides_only_touches_matching_field():
    combos = selected_combinations(_profile())
    per_serving = next(c for c in combos if c.metadata["serving_basis"] == "per serving")
    fields = [
        {"name": "Calories", "selector": "td.cal", "type": "number", "selected": True},
        {"name": "Food", "selector": "td.food", "type": "string", "selected": True},
    ]
    out = apply_field_overrides(fields, per_serving)
    assert out[0]["selector"] == "td:nth-of-type(5)"  # overridden
    assert out[1]["selector"] == "td.food"  # untouched
    # original list not mutated
    assert fields[0]["selector"] == "td.cal"


def test_metadata_columns_order():
    cols = metadata_columns(_profile())
    assert cols[0] == META_VARIANT_ID
    assert cols[1] == META_VARIANT_LABEL
    assert cols[2:] == ["serving_basis", "unit_system"]


def test_tag_record_metadata():
    combo = selected_combinations(_profile())[0]
    tagged = tag_record_metadata({"Food": "beef", "source_url": "u"}, combo)
    assert tagged["Food"] == "beef"
    assert tagged[META_VARIANT_ID] == combo.id
    assert tagged[META_VARIANT_LABEL] == combo.label
    assert tagged["serving_basis"] == combo.metadata["serving_basis"]
