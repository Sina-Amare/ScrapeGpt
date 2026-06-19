"""Phase 2.5 crawl-scope behavior tests (all four scope modes + helpers)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.job import (
    CRAWL_SCOPE_VERSION,
    CrawlScopeMode,
    DEFAULT_CRAWL_SCOPE,
    LEGACY_COMPAT_CRAWL_SCOPE,
)
from app.services.crawl_scope import (
    REASON_CURRENT_PAGE_SCOPE,
    REASON_EXCLUDED_DIFFERENT_ORIGIN,
    REASON_EXCLUDED_SCOPE_MODE,
    REASON_FULL_SITE_SAME_ORIGIN,
    REASON_PAGINATION_PATTERN_MATCH,
    REASON_DETAIL_LINK_SELECTOR_MATCH,
    classify_links_for_scope,
    default_crawl_scope,
    derive_include_patterns_from_links,
    discover_links_for_scope,
    normalize_crawl_scope,
    scope_max_depth,
    scope_max_pages,
    scope_requires_confirmation,
)


def _project(url="https://example.com/products"):
    return SimpleNamespace(url=url, normalized_url=url)


# ---- default_crawl_scope ----


def test_default_crawl_scope_uses_current_page_and_system_defaulted():
    s = default_crawl_scope(_project(), None)
    assert s["version"] == CRAWL_SCOPE_VERSION
    assert s["mode"] == CrawlScopeMode.CURRENT_PAGE.value
    assert s["status"] == "SYSTEM_DEFAULTED"
    assert s["seed_url"] == "https://example.com/products"
    assert s["max_pages"] == DEFAULT_CRAWL_SCOPE["max_pages"]


def test_default_crawl_scope_does_not_trust_analysis_only_pagination_selector():
    s = default_crawl_scope(_project(), {"pagination_selector": "a.next"})
    assert s["ai_recommendation"]["recommended_mode"] == "CURRENT_PAGE"
    assert s["ai_recommendation"]["confidence"] >= 0.5


def test_default_crawl_scope_recommends_dataset_when_ai_saw_repeated_items():
    s = default_crawl_scope(_project(), {"repeated_item_selector": ".product-card"})
    assert s["ai_recommendation"]["recommended_mode"] == "DATASET"


def test_default_crawl_scope_recommends_current_page_when_no_signal():
    s = default_crawl_scope(_project(), {})
    assert s["ai_recommendation"]["recommended_mode"] == "CURRENT_PAGE"


# ---- normalize_crawl_scope ----


def test_normalize_crawl_scope_fills_missing_fields():
    out = normalize_crawl_scope(None, seed_url="https://example.com")
    assert out["mode"] == LEGACY_COMPAT_CRAWL_SCOPE["mode"]
    assert out["status"] == "SYSTEM_DEFAULTED"
    assert out["seed_url"] == "https://example.com"


def test_normalize_crawl_scope_clamps_page_limit():
    out = normalize_crawl_scope({}, page_limit=99_999)
    assert out["max_pages"] == 5000


def test_normalize_crawl_scope_does_not_lower_existing_page_limit():
    out = normalize_crawl_scope({"max_pages": 50}, page_limit=99_999)
    assert out["max_pages"] == 5000


# ---- scope_requires_confirmation ----


def test_scope_current_page_does_not_require_confirmation():
    assert scope_requires_confirmation({"mode": "CURRENT_PAGE", "status": "AI_SUGGESTED"}) is False


def test_scope_user_confirmed_does_not_require_confirmation():
    assert scope_requires_confirmation({"mode": "PAGINATION", "status": "USER_CONFIRMED"}) is False


def test_scope_ai_suggested_pagination_requires_confirmation():
    assert scope_requires_confirmation({"mode": "PAGINATION", "status": "AI_SUGGESTED"}) is True


def test_scope_full_site_ai_suggested_requires_confirmation():
    assert scope_requires_confirmation({"mode": "FULL_SITE", "status": "AI_SUGGESTED"}) is True


def test_scope_none_means_current_page_no_confirmation():
    assert scope_requires_confirmation(None) is False


# ---- scope_max_pages / max_depth ----


def test_scope_max_pages_handles_missing_or_garbage():
    assert scope_max_pages(None) == 500
    assert scope_max_pages({}) == 500
    assert scope_max_pages({"max_pages": "not-a-number"}) == 500
    assert scope_max_pages({"max_pages": -3}) == 1
    assert scope_max_pages({"max_pages": 50}) == 50


def test_scope_max_depth_returns_none_when_unset_or_bad():
    assert scope_max_depth(None) is None
    assert scope_max_depth({}) is None
    assert scope_max_depth({"max_depth": "bad"}) is None
    assert scope_max_depth({"max_depth": -1}) is None
    assert scope_max_depth({"max_depth": 2}) == 2


# ---- CURRENT_PAGE ----


def test_current_page_scope_inserts_no_discovered_links():
    html = '<a href="/p?page=2">N</a><a href="/c/m">M</a><a href="https://other.example.com/x">O</a>'
    s = {"mode": "CURRENT_PAGE", "status": "USER_CONFIRMED"}
    d = classify_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    assert all(x.decision == "excluded" for x in d)
    category = next(x for x in d if "/c/m" in x.normalized_url)
    assert category.reason_code == REASON_CURRENT_PAGE_SCOPE
    pagination = next(x for x in d if "page=2" in x.normalized_url)
    assert pagination.reason_code == REASON_CURRENT_PAGE_SCOPE


# ---- PAGINATION ----


def test_pagination_scope_includes_only_pagination_param_urls():
    html = (
        '<a href="/p?page=2">N</a><a href="/p?page=3">3</a>'
        '<a href="/c/m">M</a><a href="/c/p">P</a><a href="/a">A</a>'
    )
    s = {"mode": "PAGINATION", "status": "USER_CONFIRMED"}
    d = classify_links_for_scope(html, page_url="https://example.com/p", root_url="https://example.com", scope=s)
    inc = {x.normalized_url for x in d if x.decision == "included"}
    assert "https://example.com/p?page=2" in inc
    assert "https://example.com/p?page=3" in inc
    assert "https://example.com/c/m" not in inc
    assert "https://example.com/c/p" not in inc
    assert "https://example.com/a" not in inc
    category = next(x for x in d if "/c/m" in x.normalized_url)
    assert category.reason_code == REASON_EXCLUDED_SCOPE_MODE


def test_pagination_scope_uses_url_pattern_when_provided():
    html = '<a href="/items/page/7">7</a><a href="/other">O</a>'
    s = {"mode": "PAGINATION", "status": "USER_CONFIRMED", "pagination": {"url_pattern": "/items/page/*"}}
    d = classify_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    assert any(x.decision == "included" and x.reason_code == REASON_PAGINATION_PATTERN_MATCH for x in d)
    assert all(x.decision == "excluded" for x in d if "other" in x.normalized_url)


# ---- DATASET ----


def test_dataset_scope_includes_only_include_patterns_and_pagination():
    html = (
        '<a href="/p?page=2">N</a><a href="/p/potato">Po</a>'
        '<a href="/p/meat">Me</a><a href="/c/pizza">P</a>'
    )
    s = {"mode": "DATASET", "status": "USER_CONFIRMED", "include_patterns": ["/p/*"], "pagination": {}}
    d = classify_links_for_scope(html, page_url="https://example.com/p", root_url="https://example.com", scope=s)
    inc = {x.normalized_url for x in d if x.decision == "included"}
    assert "https://example.com/p?page=2" in inc
    assert "https://example.com/p/potato" in inc
    assert "https://example.com/p/meat" in inc
    assert "https://example.com/c/pizza" not in inc


def test_dataset_scope_honors_detail_link_rule_pattern():
    html = '<a href="/p/potato">Po</a><a href="/product-detail/123">D</a><a href="/c/other">O</a>'
    s = {
        "mode": "DATASET",
        "status": "USER_CONFIRMED",
        "include_patterns": ["/p/*"],
        "link_rules": [{"role": "detail", "pattern": "/product-detail/*", "action": "include"}],
    }
    d = classify_links_for_scope(html, page_url="https://example.com/p", root_url="https://example.com", scope=s)
    inc = {x.normalized_url for x in d if x.decision == "included"}
    assert "https://example.com/product-detail/123" in inc
    detail = next(x for x in d if "product-detail" in x.normalized_url and x.decision == "included")
    assert detail.reason_code == REASON_DETAIL_LINK_SELECTOR_MATCH


# ---- FULL_SITE ----


def test_full_site_scope_keeps_legacy_broad_behavior_when_no_patterns():
    html = '<a href="/p?page=2">N</a><a href="/c/m">M</a><a href="/a">A</a><a href="https://other.example.com/x">O</a>'
    s = {"mode": "FULL_SITE", "status": "USER_CONFIRMED"}
    d = classify_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    inc = {x.normalized_url for x in d if x.decision == "included"}
    assert "https://example.com/p?page=2" in inc
    assert "https://example.com/c/m" in inc
    assert "https://example.com/a" in inc
    assert all(x.decision == "excluded" and x.reason_code == REASON_EXCLUDED_DIFFERENT_ORIGIN
               for x in d if "other.example.com" in x.normalized_url)
    assert any(x.decision == "included" and x.reason_code == REASON_FULL_SITE_SAME_ORIGIN for x in d)


def test_full_site_scope_respects_exclude_patterns_even_in_legacy_compat():
    html = '<a href="/p/potato">Po</a><a href="/p/meat">Me</a>'
    s = {"mode": "FULL_SITE", "status": "USER_CONFIRMED", "exclude_patterns": ["/p/meat*"]}
    d = classify_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    inc = {x.normalized_url for x in d if x.decision == "included"}
    assert "https://example.com/p/potato" in inc
    assert "https://example.com/p/meat" not in inc


def test_full_site_scope_with_include_patterns_only_inserts_matching():
    html = '<a href="/b/x">B</a><a href="/a">A</a>'
    s = {"mode": "FULL_SITE", "status": "USER_CONFIRMED", "include_patterns": ["/b/*"]}
    d = classify_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    inc = {x.normalized_url for x in d if x.decision == "included"}
    assert inc == {"https://example.com/b/x"}


# ---- navigation / different-origin / dedupe ----


def test_classify_skips_mailto_tel_javascript_anchors():
    html = '<a href="mailto:a@b.c">M</a><a href="tel:1">T</a><a href="javascript:void(0)">J</a><a href="#s">H</a>'
    s = {"mode": "FULL_SITE", "status": "USER_CONFIRMED"}
    d = classify_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    assert all(x.decision == "excluded" for x in d)
    assert all(x.reason_code.startswith("EXCLUDED_NAVIGATION") or x.reason_code == "EXCLUDED_SCOPE_MODE" for x in d)


def test_classify_dedupes_repeated_links():
    html = '<a href="/a">A</a><a href="/a">A2</a><a href="/b">B</a>'
    s = {"mode": "FULL_SITE", "status": "USER_CONFIRMED"}
    d = classify_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    urls = [x.normalized_url for x in d]
    assert urls.count("https://example.com/a") == 1
    assert urls.count("https://example.com/b") == 1


# ---- discover_links_for_scope ----


def test_discover_links_for_scope_returns_only_included_urls():
    html = '<a href="/p?page=2">N</a><a href="/c/m">M</a><a href="https://other.example.com/x">O</a>'
    s = {"mode": "PAGINATION", "status": "USER_CONFIRMED"}
    urls = discover_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s)
    assert "https://example.com/p?page=2" in urls
    assert "https://example.com/c/m" not in urls
    assert "https://other.example.com/x" not in urls


def test_discover_links_for_scope_caps_at_limit():
    html = "".join(f'<a href="/p?page={i}">{i}</a>' for i in range(50))
    s = {"mode": "PAGINATION", "status": "USER_CONFIRMED"}
    urls = discover_links_for_scope(html, page_url="https://example.com", root_url="https://example.com", scope=s, limit=10)
    assert len(urls) == 10


# ---- scope confirmation enforcement (block 1) ----

from app.services.crawl_scope import (  # noqa: E402
    ScopeConfirmationError,
    assert_scope_confirmed,
)


def test_assert_scope_confirmed_passes_for_current_page():
    assert_scope_confirmed({"mode": "CURRENT_PAGE", "status": "AI_SUGGESTED"})


def test_assert_scope_confirmed_passes_for_user_confirmed_pagination():
    assert_scope_confirmed({"mode": "PAGINATION", "status": "USER_CONFIRMED"})


def test_assert_scope_confirmed_passes_for_user_confirmed_full_site():
    assert_scope_confirmed({"mode": "FULL_SITE", "status": "USER_CONFIRMED"})


def test_assert_scope_confirmed_rejects_unconfirmed_pagination():
    with pytest.raises(ScopeConfirmationError) as exc:
        assert_scope_confirmed({"mode": "PAGINATION", "status": "AI_SUGGESTED"})
    assert exc.value.code == "SCOPE_NOT_CONFIRMED"
    assert "PAGINATION" in str(exc.value)


def test_assert_scope_confirmed_rejects_unconfirmed_full_site():
    with pytest.raises(ScopeConfirmationError) as exc:
        assert_scope_confirmed({"mode": "FULL_SITE", "status": "SYSTEM_DEFAULTED"})
    assert exc.value.code == "SCOPE_NOT_CONFIRMED"


def test_assert_scope_confirmed_rejects_unconfirmed_dataset():
    with pytest.raises(ScopeConfirmationError):
        assert_scope_confirmed({"mode": "DATASET", "status": "AI_SUGGESTED"})


def test_assert_scope_confirmed_legacy_missing_passes_by_default():
    assert_scope_confirmed(None)


def test_assert_scope_confirmed_legacy_missing_rejected_when_disallowed():
    with pytest.raises(ScopeConfirmationError) as exc:
        assert_scope_confirmed(None, allow_legacy_missing=False)
    assert exc.value.code == "SCOPE_MISSING"


def test_assert_scope_confirmed_allow_unconfirmed_short_circuits():
    assert_scope_confirmed(
        {"mode": "PAGINATION", "status": "AI_SUGGESTED"},
        allow_unconfirmed=True,
    )


# ---- derive_include_patterns_from_links (self-config from real seed links) ----

_SIBLINGS_HTML = (
    '<a href="/food/meat">Meat</a><a href="/food/fish">Fish</a>'
    '<a href="/food/fruit">Fruit</a><a href="/food/beer">Beer</a>'
)


def test_derive_collection_patterns_from_sibling_links():
    scope = {"mode": "COLLECTION", "include_patterns": [], "link_rules": []}
    out = derive_include_patterns_from_links(
        scope, html=_SIBLINGS_HTML, seed_url="https://example.com/food/beef-veal"
    )
    assert out == ["/food/*"]


def test_derive_collection_returns_none_without_sibling_cluster():
    scope = {"mode": "COLLECTION", "include_patterns": [], "link_rules": []}
    html = '<a href="/about">About</a><a href="/contact">Contact</a>'
    out = derive_include_patterns_from_links(
        scope, html=html, seed_url="https://example.com/food/beef-veal"
    )
    assert out is None


def test_derive_dataset_only_from_detail_link_evidence():
    scope = {"mode": "DATASET", "include_patterns": [], "link_rules": []}
    html = (
        '<div class="item"><a href="/item/1">1</a></div>'
        '<div class="item"><a href="/item/2">2</a></div>'
        '<div class="item"><a href="/item/3">3</a></div>'
    )
    out = derive_include_patterns_from_links(
        scope,
        html=html,
        seed_url="https://example.com/list",
        analysis={"detail_link_selector": "div.item a"},
    )
    assert out == ["/item/*"]


def test_derive_dataset_returns_none_without_detail_evidence():
    """Amendment: DATASET must NOT auto-derive a sibling glob — even when sibling
    list links are present — unless there is real detail-link evidence."""
    scope = {"mode": "DATASET", "include_patterns": [], "link_rules": []}
    out = derive_include_patterns_from_links(
        scope, html=_SIBLINGS_HTML, seed_url="https://example.com/food/beef-veal",
        analysis={},
    )
    assert out is None


def test_derive_never_overrides_existing_patterns():
    scope = {"mode": "COLLECTION", "include_patterns": ["/x/*"], "link_rules": []}
    out = derive_include_patterns_from_links(
        scope, html=_SIBLINGS_HTML, seed_url="https://example.com/food/beef-veal"
    )
    assert out is None
