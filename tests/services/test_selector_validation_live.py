"""Live E2E tests for selector validation against real websites.

These tests fetch real URLs, run validate_selectors_against_html on
the actual HTML, and assert that:
  - known-good selectors for each site are NOT flagged
  - known-bad selectors ARE flagged (required→False, confidence≤0.3)
  - the arxiv bug (p.title.is-5 a → zero-match) is detected and fixed

Run with:
    RUN_SELECTOR_LIVE=1 python -m pytest tests/services/test_selector_validation_live.py -v -s

These tests make real HTTP requests and are skipped in CI by default.
"""

from __future__ import annotations

import os

import pytest

_LIVE = pytest.mark.skipif(
    os.environ.get("RUN_SELECTOR_LIVE") != "1",
    reason="Live network test — set RUN_SELECTOR_LIVE=1 to run",
)


# ---------------------------------------------------------------------------
# arxiv.org — the site that exposed the original bug
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@_LIVE
async def test_arxiv_good_selectors_not_flagged():
    """Selectors that genuinely match arxiv results must pass validation."""
    from app.services.fetcher import fetch_url
    from app.services.extraction_spec_service import validate_selectors_against_html

    url = (
        "https://arxiv.org/search/advanced?advanced=&terms-0-operator=AND"
        "&terms-0-term=neural+network&terms-0-field=all"
        "&classification-statistics=y&date-filter_by=past_12"
        "&abstracts=show&size=25&order=-announced_date_first"
    )
    result = await fetch_url(url, "AUTO")

    analysis = {
        "repeated_item_selector": ".arxiv-result",
        "candidate_fields": [
            {"name": "title",       "selector": "p.title.is-5",                      "required": True,  "confidence": 0.96, "warnings": []},
            {"name": "authors",     "selector": "p.authors",                          "required": True,  "confidence": 0.78, "warnings": []},
            {"name": "detail_url",  "selector": "p.list-title.is-inline-block > a",   "required": True,  "confidence": 0.94, "warnings": []},
            {"name": "category",    "selector": "span.tag.is-small",                  "required": False, "confidence": 0.80, "warnings": []},
            {"name": "date",        "selector": "p.is-size-7",                        "required": False, "confidence": 0.70, "warnings": []},
        ],
        "confidence": 0.9,
        "warnings": [],
    }
    validated = validate_selectors_against_html(analysis, result.html)

    for field in validated["candidate_fields"]:
        assert field["warnings"] == [], (
            f"Field '{field['name']}' unexpectedly flagged: {field['warnings']}"
        )
    assert validated["confidence"] == 0.9


@pytest.mark.asyncio
@_LIVE
async def test_arxiv_broken_detail_url_selector_is_caught():
    """The original bug: p.title.is-5 a returns nothing on arxiv.
    Validation must catch it, demote required, and add a warning.
    """
    from app.services.fetcher import fetch_url
    from app.services.extraction_spec_service import validate_selectors_against_html

    url = (
        "https://arxiv.org/search/advanced?advanced=&terms-0-operator=AND"
        "&terms-0-term=neural+network&terms-0-field=all"
        "&classification-statistics=y&date-filter_by=past_12"
        "&abstracts=show&size=25&order=-announced_date_first"
    )
    result = await fetch_url(url, "AUTO")

    analysis = {
        "repeated_item_selector": ".arxiv-result",
        "candidate_fields": [
            {
                "name": "detail_url",
                "selector": "p.title.is-5 a",   # THE ORIGINAL BROKEN SELECTOR
                "required": True,
                "confidence": 0.94,
                "warnings": [],
            }
        ],
        "confidence": 0.9,
        "warnings": [],
    }
    validated = validate_selectors_against_html(analysis, result.html)
    field = validated["candidate_fields"][0]

    assert field["required"] is False, (
        "broken selector must be demoted to non-required"
    )
    assert field["confidence"] <= 0.3
    assert any("matched no elements" in w for w in field["warnings"])


@pytest.mark.asyncio
@_LIVE
async def test_arxiv_full_pipeline_extracts_records():
    """End-to-end: fetch arxiv → validate → extract_records_from_html returns rows.
    This is the acceptance test for the original bug report (project #68 pattern).
    """
    from app.services.fetcher import fetch_url
    from app.services.extraction_spec_service import validate_selectors_against_html
    from app.services.extractor import extract_records_from_html
    from app.models.job import ExtractionMode
    from types import SimpleNamespace

    url = (
        "https://arxiv.org/search/advanced?advanced=&terms-0-operator=AND"
        "&terms-0-term=machine+learning&terms-0-field=all"
        "&classification-statistics=y&date-filter_by=past_12"
        "&abstracts=show&size=25&order=-announced_date_first"
    )
    fetched = await fetch_url(url, "AUTO")

    analysis = {
        "repeated_item_selector": ".arxiv-result",
        "candidate_fields": [
            {"name": "title",      "selector": "p.title.is-5",                     "required": True,  "confidence": 0.96, "sample_values": [], "warnings": [], "data_type": "string", "label": "Paper Title"},
            {"name": "authors",    "selector": "p.authors",                         "required": True,  "confidence": 0.78, "sample_values": [], "warnings": [], "data_type": "string", "label": "Authors"},
            {"name": "detail_url", "selector": "p.list-title.is-inline-block > a",  "required": True,  "confidence": 0.94, "sample_values": [], "warnings": [], "data_type": "url",    "label": "Detail URL"},
        ],
        "confidence": 0.9,
        "warnings": [],
        "estimated_pages": 4,
        "pagination_selector": "a.pagination-next",
        "detail_link_selector": None,
    }
    validate_selectors_against_html(analysis, fetched.html)

    project = SimpleNamespace(
        extraction_mode=ExtractionMode.STRUCTURED,
        analysis=analysis,
        render_mode=SimpleNamespace(value="AUTO"),
        normalized_url=url,
        url=url,
        id=9999,
        user_id=1,
    )

    from app.models.job import ExtractionSpec
    spec = ExtractionSpec(
        project_id=9999,
        mode=ExtractionMode.STRUCTURED,
        fields=[
            {
                "name": "title",
                "label": "Paper Title",
                "user_label": "Paper Title",
                "selector": "p.title.is-5",
                "type": "string",
                "selected": True,
                "required": True,
                "confidence": 0.96,
                "sample_values": [],
                "warnings": [],
            },
            {
                "name": "detail_url",
                "label": "Detail URL",
                "user_label": "Detail URL",
                "selector": "p.list-title.is-inline-block > a",
                "type": "url",
                "selected": True,
                "required": True,
                "confidence": 0.94,
                "sample_values": [],
                "warnings": [],
            },
        ],
        content_config={},
        url_patterns=[],
        page_limit=500,
        export_format="csv",
        crawl_scope={},
    )

    records = extract_records_from_html(
        fetched.html,
        source_url=url,
        project=project,
        spec=spec,
        max_records=10,
    )
    assert len(records) >= 5, (
        f"Expected ≥5 records from arxiv, got {len(records)}"
    )
    for r in records:
        assert r.normalized_data.get("Paper Title"), "Every record must have a title"
        assert r.normalized_data.get("Detail URL", "").startswith("https://arxiv.org/abs/"), (
            f"Bad detail URL: {r.normalized_data.get('Detail URL')}"
        )


# ---------------------------------------------------------------------------
# news.ycombinator.com (Hacker News) — stable public site, no auth needed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@_LIVE
async def test_hackernews_good_selectors_not_flagged():
    """Hacker News is a stable public site. Known-good selectors must pass."""
    from app.services.fetcher import fetch_url
    from app.services.extraction_spec_service import validate_selectors_against_html

    result = await fetch_url("https://news.ycombinator.com/", "AUTO")

    # HN title is inside tr.athing; score is in the NEXT tr (subtext row),
    # so only test selectors that are genuinely scoped to tr.athing.
    analysis = {
        "repeated_item_selector": "tr.athing",
        "candidate_fields": [
            {"name": "title", "selector": "span.titleline a", "required": True, "confidence": 0.95, "warnings": []},
            {"name": "rank",  "selector": "span.rank",        "required": False, "confidence": 0.80, "warnings": []},
        ],
        "confidence": 0.95,
        "warnings": [],
    }
    validated = validate_selectors_against_html(analysis, result.html)

    for field in validated["candidate_fields"]:
        assert field["warnings"] == [], (
            f"Field '{field['name']}' unexpectedly flagged: {field['warnings']}"
        )


@pytest.mark.asyncio
@_LIVE
async def test_hackernews_broken_selector_caught():
    """A selector that doesn't exist on HN must be caught and demoted."""
    from app.services.fetcher import fetch_url
    from app.services.extraction_spec_service import validate_selectors_against_html

    result = await fetch_url("https://news.ycombinator.com/", "AUTO")

    analysis = {
        "repeated_item_selector": "tr.athing",
        "candidate_fields": [
            {
                "name": "author_avatar",
                "selector": "img.avatar",   # does not exist on HN
                "required": True,
                "confidence": 0.80,
                "warnings": [],
            }
        ],
        "confidence": 0.9,
        "warnings": [],
    }
    validated = validate_selectors_against_html(analysis, result.html)
    field = validated["candidate_fields"][0]

    assert field["required"] is False
    assert field["confidence"] <= 0.3
    assert any("matched no elements" in w for w in field["warnings"])


@pytest.mark.asyncio
@_LIVE
async def test_hackernews_full_extraction():
    """Full pipeline on HN front page — must return ≥20 story records."""
    from app.services.fetcher import fetch_url
    from app.services.extraction_spec_service import validate_selectors_against_html
    from app.services.extractor import extract_records_from_html
    from app.models.job import ExtractionMode, ExtractionSpec
    from types import SimpleNamespace

    url = "https://news.ycombinator.com/"
    fetched = await fetch_url(url, "AUTO")

    analysis = {
        "repeated_item_selector": "tr.athing",
        "candidate_fields": [],
        "confidence": 0.95,
        "warnings": [],
        "estimated_pages": 1,
        "pagination_selector": "a.morelink",
        "detail_link_selector": "span.titleline a",
    }
    validate_selectors_against_html(analysis, fetched.html)

    project = SimpleNamespace(
        extraction_mode=ExtractionMode.STRUCTURED,
        analysis=analysis,
        render_mode=SimpleNamespace(value="AUTO"),
        normalized_url=url,
        url=url,
        id=9998,
        user_id=1,
    )
    spec = ExtractionSpec(
        project_id=9998,
        mode=ExtractionMode.STRUCTURED,
        fields=[
            {"name": "title", "label": "Title", "user_label": "Title", "selector": "span.titleline a", "type": "string", "selected": True, "required": True, "confidence": 0.95, "sample_values": [], "warnings": []},
        ],
        content_config={},
        url_patterns=[],
        page_limit=500,
        export_format="csv",
        crawl_scope={},
    )

    records = extract_records_from_html(
        fetched.html, source_url=url, project=project, spec=spec, max_records=50
    )
    assert len(records) >= 20, f"Expected ≥20 HN stories, got {len(records)}"
    for r in records:
        assert r.normalized_data.get("Title"), "Every record must have a title"
