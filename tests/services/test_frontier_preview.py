"""Phase 2.5 frontier preview tests: persistence + warning correctness."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.crawl_scope import (
    REASON_EXCLUDED_SCOPE_MODE,
    REASON_PAGINATION_PATTERN_MATCH,
)
from app.services.frontierpreview import (
    SCOPE_EXCLUSION_THRESHOLD,
    build_frontier_preview_from_fetch,
)


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        url="https://example.com/potato-products",
        normalized_url="https://example.com/potato-products",
        analysis=None,
    )


def _spec(*, mode: str, status: str = "USER_CONFIRMED", **extras: Any) -> SimpleNamespace:
    scope: dict[str, Any] = {"mode": mode, "status": status}
    scope.update(extras)
    return SimpleNamespace(id=1, project_id=1, crawl_scope=scope)


# ---- build_frontier_preview_from_fetch (no DB, no fetch) ----


def test_build_returns_none_when_spec_is_none():
    project = _project()
    assert build_frontier_preview_from_fetch(project, None, "<html></html>") is None


def test_build_returns_none_when_seed_url_is_invalid():
    project = SimpleNamespace(id=1, url="", normalized_url="", analysis=None)
    spec = _spec(mode="PAGINATION")
    assert build_frontier_preview_from_fetch(project, spec, "<html></html>") is None


def test_build_pagination_preview_excludes_categories_and_counts_them():
    """The unrelated_same_origin_count must reflect same-origin scope
    exclusions (e.g. category links under PAGINATION), not
    different-origin links. This is the Block 3 fix."""
    html = (
        '<a href="/potato-products?page=2">Next</a>'
        '<a href="/potato-products?page=3">3</a>'
        '<a href="/food/pizza">Pizza</a>'
        '<a href="/food/meat">Meat</a>'
        '<a href="/food/beer">Beer</a>'
        '<a href="https://other.example.com/x">Other</a>'
    )
    spec = _spec(mode="PAGINATION")
    preview = build_frontier_preview_from_fetch(_project(), spec, html)
    assert preview is not None
    included = [d["normalized_url"] for d in preview.included_urls if d.get("role") != "seed"]
    excluded = [d["normalized_url"] for d in preview.excluded_urls]
    # Included: two pagination URLs.
    assert "https://example.com/potato-products?page=2" in included
    assert "https://example.com/potato-products?page=3" in included
    # Excluded: three same-origin category links + one different-origin.
    assert "https://example.com/food/pizza" in excluded
    assert "https://example.com/food/meat" in excluded
    assert "https://example.com/food/beer" in excluded
    assert "https://other.example.com/x" in excluded
    # unrelated_same_origin_count must count the three category links,
    # NOT the one different-origin link. The different-origin link
    # has reason_code EXCLUDED_DIFFERENT_ORIGIN, not EXCLUDED_SCOPE_MODE.
    assert preview.quality_summary["unrelated_same_origin_count"] == 3


def test_build_pagination_preview_emits_warning_when_categories_exceed_threshold():
    """With SCOPE_EXCLUSION_THRESHOLD=10, we need >= 10 same-origin
    scope exclusions to emit the FRONTIER_HAS_MANY_EXCLUSIONS warning."""
    # 12 category links
    cats = "".join(f'<a href="/food/cat{i}">C{i}</a>' for i in range(12))
    # 1 pagination link
    html = f'<a href="/list?page=2">N</a>{cats}'
    spec = _spec(mode="PAGINATION")
    preview = build_frontier_preview_from_fetch(_project(), spec, html)
    assert preview is not None
    assert preview.quality_summary["unrelated_same_origin_count"] == 12
    codes = [w["code"] for w in preview.warnings]
    assert "FRONTIER_HAS_MANY_EXCLUSIONS" in codes
    warn = next(w for w in preview.warnings if w["code"] == "FRONTIER_HAS_MANY_EXCLUSIONS")
    assert warn["count"] == 12
    assert "12" in warn["message"]


def test_build_pagination_preview_does_not_warn_below_threshold():
    cats = "".join(f'<a href="/food/cat{i}">C{i}</a>' for i in range(5))
    html = f'<a href="/list?page=2">N</a>{cats}'
    spec = _spec(mode="PAGINATION")
    preview = build_frontier_preview_from_fetch(_project(), spec, html)
    assert preview is not None
    assert preview.quality_summary["unrelated_same_origin_count"] == 5
    assert preview.warnings == []


def test_build_full_site_preview_does_not_count_categories_as_scope_exclusions():
    """FULL_SITE includes every same-origin link, so there are no
    scope-mode exclusions. The category links are not 'unrelated' to
    this mode; they are valid includes."""
    html = (
        '<a href="/list?page=2">N</a>'
        '<a href="/food/pizza">Pizza</a>'
        '<a href="/food/meat">Meat</a>'
        '<a href="/food/beer">Beer</a>'
    )
    spec = _spec(mode="FULL_SITE")
    preview = build_frontier_preview_from_fetch(_project(), spec, html)
    assert preview is not None
    assert preview.quality_summary["unrelated_same_origin_count"] == 0
    # All four links are included.
    inc = [d["normalized_url"] for d in preview.included_urls if d.get("role") != "seed"]
    assert len(inc) == 4


def test_build_dataset_preview_counts_only_scope_mode_exclusions():
    """Under DATASET, only the include_patterns + pagination are
    included. Same-origin links that miss both (e.g. /food/* when the
    include pattern is /p/*) are EXCLUDED_SCOPE_MODE and must be
    counted."""
    html = (
        '<a href="/p?page=2">N</a>'
        '<a href="/p/potato">Po</a>'
        '<a href="/c/pizza">P</a>'
        '<a href="/c/meat">M</a>'
        '<a href="/c/beer">B</a>'
        '<a href="https://other.example.com/x">O</a>'
    )
    spec = _spec(mode="DATASET", include_patterns=["/p/*"], pagination={})
    preview = build_frontier_preview_from_fetch(_project(), spec, html)
    assert preview is not None
    assert preview.quality_summary["unrelated_same_origin_count"] == 3
    # Cross-origin must NOT be counted.
    cross = [d for d in preview.excluded_urls if "other.example.com" in d["normalized_url"]]
    assert cross
    assert cross[0]["reason_code"] == "EXCLUDED_DIFFERENT_ORIGIN"


def test_build_current_page_preview_only_emits_seed():
    html = (
        '<a href="/p?page=2">N</a>'
        '<a href="/c/pizza">P</a>'
        '<a href="https://other.example.com/x">O</a>'
    )
    spec = _spec(mode="CURRENT_PAGE")
    preview = build_frontier_preview_from_fetch(_project(), spec, html)
    assert preview is not None
    # Only the seed is in included.
    assert len([d for d in preview.included_urls if d.get("role") != "seed"]) == 0
    # The two same-origin links are EXCLUDED_SCOPE_MODE; they DO count.
    assert preview.quality_summary["unrelated_same_origin_count"] == 2


def test_build_seed_url_always_first_in_included_when_no_pagination_match():
    html = '<a href="/c/pizza">P</a><a href="/c/meat">M</a>'
    spec = _spec(mode="FULL_SITE")
    preview = build_frontier_preview_from_fetch(_project(), spec, html)
    assert preview is not None
    assert preview.included_urls[0]["reason_code"] == "SEED_URL"


# ---- ScopeConfirmationError: project_extraction seam ----


def test_select_links_to_enqueue_integration_for_all_four_modes():
    """Integration test: the seam ``project_extraction.select_links_to_enqueue``
    must agree with the classifier for every scope mode. This is the
    smallest layer that proves extraction queues pages according to
    scope (Block 5). It does not touch the network, the DB, or the
    background executor."""
    from app.services.project_extraction import select_links_to_enqueue

    html = (
        '<a href="/p?page=2">N</a>'
        '<a href="/p?page=3">3</a>'
        '<a href="/c/pizza">P</a>'
        '<a href="/c/meat">M</a>'
        '<a href="/food-detail/1">D1</a>'
        '<a href="https://other.example.com/x">O</a>'
    )

    # CURRENT_PAGE: no links queued regardless of the html.
    current = select_links_to_enqueue(
        html=html,
        page_url="https://example.com/p",
        root_url="https://example.com",
        scope={"mode": "CURRENT_PAGE", "status": "USER_CONFIRMED"},
        remaining_slots=20,
    )
    assert current == []

    # PAGINATION: only pagination URLs.
    pagination = select_links_to_enqueue(
        html=html,
        page_url="https://example.com/p",
        root_url="https://example.com",
        scope={"mode": "PAGINATION", "status": "USER_CONFIRMED"},
        remaining_slots=20,
    )
    assert set(pagination) == {
        "https://example.com/p?page=2",
        "https://example.com/p?page=3",
    }

    # DATASET: pagination + include_patterns.
    dataset = select_links_to_enqueue(
        html=html,
        page_url="https://example.com/p",
        root_url="https://example.com",
        scope={
            "mode": "DATASET",
            "status": "USER_CONFIRMED",
            "include_patterns": ["/p/*"],
            "pagination": {},
        },
        remaining_slots=20,
    )
    assert set(dataset) == {
        "https://example.com/p?page=2",
        "https://example.com/p?page=3",
    }

    # DATASET with detail rule: pagination + /p/* + /food-detail/*
    dataset_detail = select_links_to_enqueue(
        html=html,
        page_url="https://example.com/p",
        root_url="https://example.com",
        scope={
            "mode": "DATASET",
            "status": "USER_CONFIRMED",
            "include_patterns": ["/p/*"],
            "link_rules": [
                {"role": "detail", "pattern": "/food-detail/*", "action": "include"},
            ],
            "pagination": {},
        },
        remaining_slots=20,
    )
    assert "https://example.com/food-detail/1" in dataset_detail
    assert "https://example.com/c/pizza" not in dataset_detail

    # FULL_SITE without patterns: every same-origin link.
    full = select_links_to_enqueue(
        html=html,
        page_url="https://example.com/p",
        root_url="https://example.com",
        scope={"mode": "FULL_SITE", "status": "USER_CONFIRMED"},
        remaining_slots=20,
    )
    assert "https://example.com/p?page=2" in full
    assert "https://example.com/p?page=3" in full
    assert "https://example.com/c/pizza" in full
    assert "https://example.com/c/meat" in full
    assert "https://example.com/food-detail/1" in full
    assert "https://other.example.com/x" not in full

    # FULL_SITE with include patterns: only those.
    full_inc = select_links_to_enqueue(
        html=html,
        page_url="https://example.com/p",
        root_url="https://example.com",
        scope={
            "mode": "FULL_SITE",
            "status": "USER_CONFIRMED",
            "include_patterns": ["/c/*"],
        },
        remaining_slots=20,
    )
    assert set(full_inc) == {
        "https://example.com/c/pizza",
        "https://example.com/c/meat",
    }


def test_select_links_to_enqueue_respects_remaining_slots_cap():
    from app.services.project_extraction import select_links_to_enqueue

    html = "".join(f'<a href="/p?page={i}">{i}</a>' for i in range(20))
    out = select_links_to_enqueue(
        html=html,
        page_url="https://example.com/p",
        root_url="https://example.com",
        scope={"mode": "PAGINATION", "status": "USER_CONFIRMED"},
        remaining_slots=5,
    )
    assert len(out) == 5


def test_select_links_to_enqueue_returns_empty_for_legacy_no_scope():
    from app.services.project_extraction import select_links_to_enqueue

    html = '<a href="/a">A</a><a href="/b">B</a><a href="https://other.example.com/x">O</a>'
    out = select_links_to_enqueue(
        html=html,
        page_url="https://example.com",
        root_url="https://example.com",
        scope=None,
        remaining_slots=20,
    )
    # Legacy same-site BFS includes same-origin links.
    assert "https://example.com/a" in out
    assert "https://example.com/b" in out
    assert "https://other.example.com/x" not in out


def test_too_narrow_cta_suggests_dataset_for_detail_links():
    """PAGINATION chosen, no pagination links, but the page links to many detail
    pages -> SCOPE_TOO_NARROW CTA suggesting DATASET with a derived pattern."""
    project = SimpleNamespace(
        id=1,
        url="https://example.com/list",
        normalized_url="https://example.com/list",
        analysis={"detail_link_selector": "a.detail"},
    )
    spec = _spec(mode="PAGINATION")
    items = "".join(
        f'<a class="detail" href="/item/{i}">Item {i}</a>' for i in range(1, 13)
    )
    html = f"<html><body>{items}</body></html>"
    preview = build_frontier_preview_from_fetch(project, spec, html)
    warning = next(
        (w for w in (preview.warnings or []) if w["code"] == "SCOPE_TOO_NARROW"),
        None,
    )
    assert warning is not None
    assert warning["suggested_mode"] == "DATASET"
    assert warning["suggested_include_patterns"] == ["/item/*"]
    assert warning["count"] == 12


def test_too_narrow_cta_suggests_collection_for_sibling_links():
    """CURRENT_PAGE seed with many same-origin sibling category links and no
    detail selector -> SCOPE_TOO_NARROW CTA suggesting COLLECTION."""
    project = SimpleNamespace(
        id=1,
        url="https://example.com/food/beef",
        normalized_url="https://example.com/food/beef",
        analysis=None,
    )
    spec = _spec(mode="CURRENT_PAGE")
    cats = "".join(
        f'<a href="/food/cat{i}">Cat {i}</a>' for i in range(1, 13)
    )
    html = f"<html><body>{cats}</body></html>"
    preview = build_frontier_preview_from_fetch(project, spec, html)
    warning = next(
        (w for w in (preview.warnings or []) if w["code"] == "SCOPE_TOO_NARROW"),
        None,
    )
    assert warning is not None
    assert warning["suggested_mode"] == "COLLECTION"
    assert warning["suggested_include_patterns"] == ["/food/*"]


def test_no_too_narrow_cta_without_enough_links():
    project = SimpleNamespace(
        id=1,
        url="https://example.com/list",
        normalized_url="https://example.com/list",
        analysis={"detail_link_selector": "a.detail"},
    )
    spec = _spec(mode="PAGINATION")
    html = (
        "<html><body>"
        '<a class="detail" href="/item/1">Item 1</a>'
        '<a class="detail" href="/item/2">Item 2</a>'
        "</body></html>"
    )
    preview = build_frontier_preview_from_fetch(project, spec, html)
    codes = {w["code"] for w in (preview.warnings or [])}
    assert "SCOPE_TOO_NARROW" not in codes
