"""Unit tests for validate_selectors_against_html.

Tests every branch:
- Valid selectors pass through unchanged
- Zero-match field selector → required=False, confidence capped, warning added
- Zero-match container selector → analysis confidence capped, warning added
- No container selector → validated against full page
- Empty / missing HTML or analysis → no crash
- _build_field propagates warnings from the analysis field dict
- Integration: validate_selectors_against_html → default_spec_from_analysis
  correctly surfaces zero-match fields as non-required with warnings
"""

from __future__ import annotations

import pytest

from app.services.extraction_spec_service import (
    _build_field,
    default_spec_from_analysis,
    validate_selectors_against_html,
)


# ---------------------------------------------------------------------------
# Minimal HTML fixtures
# ---------------------------------------------------------------------------

_ARXIV_LIKE_HTML = """
<html><body>
<ol class="breathe-horizontal" id="search-results">
  <li class="arxiv-result">
    <div class="is-marginless">
      <p class="list-title is-inline-block">
        <a href="https://arxiv.org/abs/2606.00001">arXiv:2606.00001</a>
        <span>[ <a href="/pdf/2606.00001">pdf</a> ]</span>
      </p>
      <div class="tags is-inline-block">
        <span class="tag is-small is-link">cs.LG</span>
        <span class="tag is-small is-grey">stat.ML</span>
      </div>
    </div>
    <p class="title is-5 mathjax">A Great Paper Title</p>
    <p class="authors">
      <span class="has-text-black-bis has-text-weight-semibold">Authors:</span>
      <a href="/search/?author=Doe">Jane Doe</a>,
      <a href="/search/?author=Smith">John Smith</a>
    </p>
    <p class="is-size-7">Submitted 1 June, 2026</p>
  </li>
  <li class="arxiv-result">
    <div class="is-marginless">
      <p class="list-title is-inline-block">
        <a href="https://arxiv.org/abs/2606.00002">arXiv:2606.00002</a>
      </p>
    </div>
    <p class="title is-5 mathjax">Another Paper</p>
    <p class="authors">
      <span class="has-text-black-bis has-text-weight-semibold">Authors:</span>
      <a href="/search/?author=Wang">Wei Wang</a>
    </p>
    <p class="is-size-7">Submitted 2 June, 2026</p>
  </li>
</ol>
</body></html>
"""

_SIMPLE_HTML = """
<html><body>
  <ul class="product-list">
    <li class="product-card">
      <h2 class="product-name">Widget Pro</h2>
      <span class="price">$9.99</span>
      <a class="product-link" href="/product/1">View</a>
    </li>
    <li class="product-card">
      <h2 class="product-name">Gadget Lite</h2>
      <span class="price">$4.99</span>
      <a class="product-link" href="/product/2">View</a>
    </li>
  </ul>
</body></html>
"""


def _analysis(
    container: str | None,
    fields: list[dict],
    confidence: float = 0.9,
) -> dict:
    return {
        "repeated_item_selector": container,
        "candidate_fields": fields,
        "confidence": confidence,
        "warnings": [],
    }


def _field(name: str, selector: str, *, required: bool = True, confidence: float = 0.9) -> dict:
    return {
        "name": name,
        "label": name.replace("_", " ").title(),
        "selector": selector,
        "data_type": "string",
        "required": required,
        "confidence": confidence,
        "sample_values": [],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Happy path: selectors that match
# ---------------------------------------------------------------------------

def test_valid_selectors_pass_through_unchanged():
    analysis = _analysis(
        ".arxiv-result",
        [
            _field("title", "p.title.is-5"),
            _field("date", "p.is-size-7", required=False, confidence=0.7),
            _field("detail_url", "p.list-title.is-inline-block > a"),
        ],
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)

    for f in result["candidate_fields"]:
        assert f["warnings"] == [], f"Unexpected warning on {f['name']}: {f['warnings']}"
    assert result["confidence"] == 0.9
    assert result["warnings"] == []


def test_container_matches_items():
    analysis = _analysis(".arxiv-result", [_field("title", "p.title.is-5")])
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    assert result["candidate_fields"][0]["required"] is True
    assert result["candidate_fields"][0]["confidence"] == 0.9


# ---------------------------------------------------------------------------
# Zero-match field selector
# ---------------------------------------------------------------------------

def test_zero_match_field_marked_non_required():
    analysis = _analysis(
        ".arxiv-result",
        [_field("detail_url", "p.title.is-5 a", required=True, confidence=0.94)],
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    f = result["candidate_fields"][0]

    assert f["required"] is False, "zero-match required field must be demoted"
    assert f["confidence"] <= 0.3
    assert len(f["warnings"]) == 1
    assert "matched no elements" in f["warnings"][0]


def test_zero_match_field_confidence_capped_at_0_3():
    analysis = _analysis(
        ".arxiv-result",
        [_field("ghost", "div.does-not-exist", required=False, confidence=0.8)],
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    f = result["candidate_fields"][0]
    assert f["confidence"] == 0.3


def test_over_specified_selector_is_relaxed_not_penalized():
    """A selector that over-specifies a missing descendant is self-healed by
    relaxing it, keeping the field's confidence (so it stays selected) instead
    of capping to 0.3. Regression: calories.info calorie cells have no <p>, so
    'td:nth-child(3) p' was penalized and deselected."""
    analysis = _analysis(
        ".arxiv-result",
        [_field("title", "p.title.is-5 span", required=False, confidence=0.9)],
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    f = result["candidate_fields"][0]
    assert f["selector"] == "p.title.is-5"  # relaxed to the matching ancestor
    assert f["confidence"] == 0.9  # preserved, not capped
    assert any("relaxed" in w for w in f["warnings"])


def test_zero_match_does_not_touch_other_fields():
    analysis = _analysis(
        ".arxiv-result",
        [
            _field("title", "p.title.is-5"),               # valid
            _field("ghost", "span.nonexistent"),            # zero-match
        ],
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    title_field = next(f for f in result["candidate_fields"] if f["name"] == "title")
    ghost_field = next(f for f in result["candidate_fields"] if f["name"] == "ghost")

    assert title_field["required"] is True
    assert title_field["warnings"] == []
    assert ghost_field["required"] is False
    assert ghost_field["confidence"] == 0.3


# ---------------------------------------------------------------------------
# Zero-match container selector
# ---------------------------------------------------------------------------

def test_zero_match_container_caps_analysis_confidence():
    analysis = _analysis(
        ".does-not-exist",
        [_field("title", "p.title.is-5")],
        confidence=0.9,
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)

    assert result["confidence"] <= 0.4
    assert any("Container selector" in w for w in result["warnings"])


def test_zero_match_container_falls_back_to_page_scope():
    """When container is missing, field validation falls back to the full page.
    p.title.is-5 exists at the page level even without .does-not-exist.
    """
    analysis = _analysis(
        ".does-not-exist",
        [_field("title", "p.title.is-5")],
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    title = result["candidate_fields"][0]
    # Should NOT be flagged as zero-match because it exists at page level
    assert title["warnings"] == []


# ---------------------------------------------------------------------------
# No container selector (None / absent)
# ---------------------------------------------------------------------------

def test_no_container_validates_against_full_page():
    analysis = _analysis(
        None,
        [
            _field("name", "h2.product-name"),
            _field("price", "span.price"),
            _field("link", "a.product-link"),
        ],
    )
    result = validate_selectors_against_html(analysis, _SIMPLE_HTML)
    for f in result["candidate_fields"]:
        assert f["warnings"] == [], f"{f['name']} should pass full-page validation"


def test_no_container_zero_match_full_page():
    analysis = _analysis(None, [_field("ghost", "div.phantom")])
    result = validate_selectors_against_html(analysis, _SIMPLE_HTML)
    f = result["candidate_fields"][0]
    assert f["required"] is False
    assert f["confidence"] == 0.3


# ---------------------------------------------------------------------------
# Edge cases: empty inputs
# ---------------------------------------------------------------------------

def test_empty_html_returns_analysis_unchanged():
    analysis = _analysis(".arxiv-result", [_field("title", "p.title.is-5")])
    result = validate_selectors_against_html(analysis, "")
    # No crash; analysis returned as-is
    assert result is analysis


def test_none_html_returns_analysis_unchanged():
    analysis = _analysis(".arxiv-result", [_field("title", "p.title.is-5")])
    result = validate_selectors_against_html(analysis, None)  # type: ignore[arg-type]
    assert result is analysis


def test_empty_analysis_returns_unchanged():
    result = validate_selectors_against_html({}, _ARXIV_LIKE_HTML)
    assert result == {}


def test_no_candidate_fields_returns_unchanged():
    analysis = {"repeated_item_selector": ".foo", "candidate_fields": [], "confidence": 0.8}
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    assert result["confidence"] == 0.8


def test_field_without_selector_skipped():
    analysis = _analysis(".arxiv-result", [{"name": "no_sel", "required": True, "confidence": 0.9}])
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    f = result["candidate_fields"][0]
    # required/confidence unchanged — no selector to validate
    assert f["required"] is True
    assert f["confidence"] == 0.9


def test_invalid_css_selector_does_not_crash():
    analysis = _analysis(
        ".arxiv-result",
        [_field("bad", "p:::invalid::pseudo")],
    )
    result = validate_selectors_against_html(analysis, _ARXIV_LIKE_HTML)
    # Should not raise; field flagged as zero-match (select() will fail → no match)
    f = result["candidate_fields"][0]
    assert f["required"] is False


# ---------------------------------------------------------------------------
# _build_field propagates warnings
# ---------------------------------------------------------------------------

def test_build_field_propagates_warnings_from_analysis():
    field_with_warning = {
        "name": "detail_url",
        "label": "Detail URL",
        "selector": "p.title.is-5 a",
        "data_type": "url",
        "required": False,
        "confidence": 0.3,
        "sample_values": [],
        "warnings": ["Selector 'p.title.is-5 a' matched no elements in the fetched HTML."],
    }
    built = _build_field(field_with_warning, 0.3)
    assert len(built["warnings"]) == 1
    assert "matched no elements" in built["warnings"][0]


def test_build_field_empty_warnings_when_analysis_has_none():
    field = {
        "name": "title",
        "label": "Title",
        "selector": "p.title.is-5",
        "data_type": "string",
        "required": True,
        "confidence": 0.96,
        "sample_values": [],
    }
    built = _build_field(field, 0.96)
    assert built["warnings"] == []


# ---------------------------------------------------------------------------
# Integration: full analysis → spec flow
# ---------------------------------------------------------------------------

def test_default_spec_from_analysis_uses_validated_fields():
    """After validate_selectors_against_html runs, default_spec_from_analysis
    must reflect the corrected required/confidence/warnings in the spec fields.
    """
    from types import SimpleNamespace
    from app.models.job import ExtractionMode

    project = SimpleNamespace(
        extraction_mode=ExtractionMode.STRUCTURED,
        analysis={
            "repeated_item_selector": ".arxiv-result",
            "candidate_fields": [
                {
                    "name": "title",
                    "label": "Paper Title",
                    "selector": "p.title.is-5",
                    "data_type": "string",
                    "required": True,
                    "confidence": 0.96,
                    "sample_values": [],
                    "warnings": [],
                },
                {
                    "name": "detail_url",
                    "label": "Detail URL",
                    "selector": "p.title.is-5 a",   # BROKEN — no <a> in title on arxiv
                    "data_type": "url",
                    "required": True,
                    "confidence": 0.94,
                    "sample_values": [],
                    "warnings": ["Selector 'p.title.is-5 a' matched no elements in the fetched HTML."],
                },
            ],
            "confidence": 0.85,
            "warnings": [],
            "estimated_pages": 6,
            "pagination_selector": "a.pagination-next",
            "detail_link_selector": None,
        },
        render_mode=SimpleNamespace(value="AUTO"),
        normalized_url="https://arxiv.org/search/advanced",
        url="https://arxiv.org/search/advanced",
        id=999,
        user_id=1,
    )

    # Simulate what job_executor does: validate then build spec
    validate_selectors_against_html(project.analysis, _ARXIV_LIKE_HTML)
    spec_data = default_spec_from_analysis(project)

    title_field = next(f for f in spec_data["fields"] if f["name"] == "title")
    detail_field = next(f for f in spec_data["fields"] if f["name"] == "detail_url")

    # title selector is valid → required stays True
    assert title_field["required"] is True
    assert title_field["warnings"] == []

    # detail_url selector was already flagged (warning pre-set) → required=False
    # _build_field reads required from the analysis field dict (which was set False by validation)
    # But in this test, validation already ran above and set required=False on the analysis dict
    assert detail_field["required"] is False
    assert len(detail_field["warnings"]) >= 1
