"""Phase 1 crawl-scope tests: COLLECTION mode, evidence-based recommender,
depth enforcement, dominant-path helpers, and spec-hash coverage."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.crawl_scope import (
    REASON_COLLECTION_PATTERN_MATCH,
    REASON_EXCLUDED_DEPTH_LIMIT,
    REASON_EXCLUDED_DIFFERENT_ORIGIN,
    REASON_EXCLUDED_SCOPE_MODE,
    classify_links_for_scope,
    default_crawl_scope,
    discover_links_for_scope,
    dominant_path_glob,
    dominant_prefix_glob,
    recommend_scope,
)
from app.schemas.project import CrawlScope


SEED = "https://www.calories.info/food/beef-veal"

CALORIES_HTML = """
<html><body>
  <a href="/food/meat">Meat</a>
  <a href="/food/beer">Beer</a>
  <a href="/food/fish">Fish</a>
  <a href="/food/fruit">Fruit</a>
  <a href="/food/beef-veal">Beef and veal</a>
  <a href="https://twitter.com/x">twitter</a>
  <a href="/about">About</a>
</body></html>
"""


# ---------------------------------------------------------------------------
# COLLECTION classifier
# ---------------------------------------------------------------------------


def _collection_scope(**extra):
    scope = {
        "mode": "COLLECTION",
        "status": "USER_CONFIRMED",
        "include_patterns": ["/food/*"],
        "exclude_patterns": [],
        "max_depth": 1,
    }
    scope.update(extra)
    return scope


def test_collection_includes_siblings_excludes_external_and_noise():
    decisions = classify_links_for_scope(
        CALORIES_HTML,
        page_url=SEED,
        root_url=SEED,
        scope=_collection_scope(),
        source_depth=0,
    )
    by_url = {d.normalized_url: d for d in decisions}

    for path in ("meat", "beer", "fish", "fruit"):
        d = by_url[f"https://www.calories.info/food/{path}"]
        assert d.decision == "included"
        assert d.role == "collection"
        assert d.reason_code == REASON_COLLECTION_PATTERN_MATCH

    # External link is dropped.
    assert by_url["https://twitter.com/x"].reason_code == REASON_EXCLUDED_DIFFERENT_ORIGIN
    # Same-origin non-matching link is "outside the scope mode".
    assert by_url["https://www.calories.info/about"].reason_code == REASON_EXCLUDED_SCOPE_MODE


def test_collection_without_include_patterns_matches_nothing():
    decisions = classify_links_for_scope(
        CALORIES_HTML,
        page_url=SEED,
        root_url=SEED,
        scope=_collection_scope(include_patterns=[]),
        source_depth=0,
    )
    included = [d for d in decisions if d.decision == "included"]
    assert included == []


# ---------------------------------------------------------------------------
# Depth enforcement
# ---------------------------------------------------------------------------


def test_collection_depth_limit_excludes_grandchildren():
    # From a depth-1 page, children would be depth 2 > max_depth 1 -> excluded.
    decisions = classify_links_for_scope(
        CALORIES_HTML,
        page_url="https://www.calories.info/food/meat",
        root_url=SEED,
        scope=_collection_scope(),
        source_depth=1,
    )
    codes = {d.reason_code for d in decisions if d.decision == "excluded"}
    assert REASON_EXCLUDED_DEPTH_LIMIT in codes
    # Nothing is enqueued past the depth bound.
    assert all(d.decision == "excluded" for d in decisions if d.role != "seed")


def test_pagination_depth_is_unbounded():
    # PAGINATION carries max_depth None/0 -> unbounded; deep pages keep following.
    html = (
        '<a href="/list?page=2">2</a>'
        '<a href="/list?page=3">3</a>'
    )
    scope = {
        "mode": "PAGINATION",
        "status": "USER_CONFIRMED",
        "max_depth": 0,  # the CURRENT_PAGE default carried across a mode switch
    }
    links = discover_links_for_scope(
        html,
        page_url="https://x.com/list",
        root_url="https://x.com/list",
        scope=scope,
        source_depth=9,  # very deep
    )
    assert "https://x.com/list?page=2" in links
    assert "https://x.com/list?page=3" in links


# ---------------------------------------------------------------------------
# Evidence-based recommender
# ---------------------------------------------------------------------------


def test_recommend_scope_collection_for_sibling_cluster():
    rec = recommend_scope({}, CALORIES_HTML, SEED)
    assert rec["recommended_mode"] == "COLLECTION"
    assert rec["suggested_include_patterns"] == ["/food/*"]


def test_recommend_scope_pagination_only_with_real_links():
    html = (
        '<a href="/list?page=2">2</a>'
        '<a href="/list?page=3">3</a>'
        '<a href="/item/a">a</a>'
    )
    rec = recommend_scope({}, html, "https://x.com/list")
    assert rec["recommended_mode"] == "PAGINATION"


def test_recommend_scope_ignores_unmatched_pagination_selector():
    """Root-cause fix: an AI-claimed pagination selector that matches no anchors
    must NOT yield a PAGINATION recommendation."""
    rec = recommend_scope(
        {"pagination_selector": "a.next-page"},  # not present in HTML
        CALORIES_HTML,
        SEED,
    )
    assert rec["recommended_mode"] != "PAGINATION"


def test_recommend_scope_pagination_detects_page_num_param():
    """scrapethissite.com-style ?page_num=N pagination must be recognised even
    though the key is not a bare 'page='."""
    html = (
        '<a href="/pages/forms/?page_num=2">2</a>'
        '<a href="/pages/forms/?page_num=3">3</a>'
    )
    rec = recommend_scope({}, html, "https://x.com/pages/forms/")
    assert rec["recommended_mode"] == "PAGINATION"


def test_recommend_scope_dataset_for_detail_links():
    items = "".join(f'<a class="d" href="/item/{i}">{i}</a>' for i in range(6))
    html = f"<html><body>{items}</body></html>"
    rec = recommend_scope(
        {"detail_link_selector": "a.d"}, html, "https://x.com/list"
    )
    assert rec["recommended_mode"] == "DATASET"


def test_recommend_scope_current_page_without_signal():
    rec = recommend_scope({}, "<html><body><p>nothing</p></body></html>", "https://x.com/p")
    assert rec["recommended_mode"] == "CURRENT_PAGE"


# ---------------------------------------------------------------------------
# dominant-path helpers
# ---------------------------------------------------------------------------


def test_dominant_path_glob_uses_seed_parent():
    urls = [
        "https://x.com/food/meat",
        "https://x.com/food/beer",
        "https://x.com/food/fish",
    ]
    assert dominant_path_glob(urls, "https://x.com/food/beef") == ("/food/*", 3)


def test_dominant_path_glob_none_for_top_level_seed():
    urls = ["https://x.com/item/1", "https://x.com/item/2"]
    assert dominant_path_glob(urls, "https://x.com/list") is None


def test_dominant_prefix_glob_clusters_by_first_segment():
    urls = [
        "https://x.com/item/1",
        "https://x.com/item/2",
        "https://x.com/item/3",
        "https://x.com/about",
    ]
    assert dominant_prefix_glob(urls) == ("/item/*", 3)


def test_dominant_prefix_glob_none_for_empty():
    assert dominant_prefix_glob([]) is None


# ---------------------------------------------------------------------------
# default_crawl_scope prefers the precomputed (HTML-validated) recommendation
# ---------------------------------------------------------------------------


def test_default_crawl_scope_prefers_precomputed_recommendation():
    analysis = {
        "scope_recommendation": {
            "recommended_mode": "COLLECTION",
            "confidence": 0.7,
            "warnings": [],
            "evidence": ["x"],
            "suggested_include_patterns": ["/food/*"],
        }
    }
    scope = default_crawl_scope(SimpleNamespace(url=SEED), analysis)
    rec = scope["ai_recommendation"]
    assert rec["recommended_mode"] == "COLLECTION"
    assert rec["suggested_include_patterns"] == ["/food/*"]


# ---------------------------------------------------------------------------
# Schema: COLLECTION normalizes max_depth to a positive bound
# ---------------------------------------------------------------------------


def test_collection_schema_defaults_max_depth_to_one():
    assert CrawlScope(mode="COLLECTION", status="AI_SUGGESTED").max_depth == 1
    # An explicit 0 (carried from the CURRENT_PAGE default) is bumped to 1.
    assert CrawlScope(mode="COLLECTION", status="AI_SUGGESTED", max_depth=0).max_depth == 1
    # A larger explicit bound is respected.
    assert CrawlScope(mode="COLLECTION", status="AI_SUGGESTED", max_depth=3).max_depth == 3


def test_pagination_schema_leaves_max_depth_unbounded():
    assert CrawlScope(mode="PAGINATION", status="AI_SUGGESTED").max_depth is None


# ---------------------------------------------------------------------------
# _spec_hash includes crawl_scope
# ---------------------------------------------------------------------------


def test_spec_hash_changes_with_crawl_scope():
    from app.services.project_extraction import _spec_hash

    base = SimpleNamespace(
        fields=[{"name": "title"}],
        content_config={},
        url_patterns=[],
        page_limit=100,
        export_format="csv",
        crawl_scope={"mode": "CURRENT_PAGE"},
    )
    broadened = SimpleNamespace(
        fields=[{"name": "title"}],
        content_config={},
        url_patterns=[],
        page_limit=100,
        export_format="csv",
        crawl_scope={"mode": "COLLECTION", "include_patterns": ["/food/*"]},
    )
    assert _spec_hash(base) != _spec_hash(broadened)
