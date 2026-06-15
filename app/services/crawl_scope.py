"""Crawl scope helpers (Workstream A, behavior layer).

Pure helpers for the Phase 2.5 crawl-scope object model. No DB, no
LLM, no HTTP. Consumed by extraction_spec_service, project_extraction,
and frontierpreview.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.models.job import (
    CRAWL_SCOPE_VERSION,
    CrawlScopeMode,
    DEFAULT_CRAWL_SCOPE,
    LEGACY_COMPAT_CRAWL_SCOPE,
)
from app.services.url_normalizer import normalize_url, same_origin


# Reason codes used by both the classifier and the inserter. Centralised
# so the frontier preview and the test suite share one source of truth.
REASON_SEED_URL = "SEED_URL"
REASON_CURRENT_PAGE_SCOPE = "CURRENT_PAGE_SCOPE"
REASON_PAGINATION_SELECTOR_MATCH = "PAGINATION_SELECTOR_MATCH"
REASON_PAGINATION_PATTERN_MATCH = "PAGINATION_PATTERN_MATCH"
REASON_DATASET_PATTERN_MATCH = "DATASET_PATTERN_MATCH"
REASON_COLLECTION_PATTERN_MATCH = "COLLECTION_PATTERN_MATCH"
REASON_DETAIL_LINK_SELECTOR_MATCH = "DETAIL_LINK_SELECTOR_MATCH"
REASON_FULL_SITE_SAME_ORIGIN = "FULL_SITE_SAME_ORIGIN"
REASON_EXCLUDED_DIFFERENT_ORIGIN = "EXCLUDED_DIFFERENT_ORIGIN"
REASON_EXCLUDED_SCOPE_MODE = "EXCLUDED_SCOPE_MODE"
REASON_EXCLUDED_PATTERN = "EXCLUDED_PATTERN"
REASON_EXCLUDED_NAVIGATION = "EXCLUDED_NAVIGATION"
REASON_EXCLUDED_PAGE_LIMIT = "EXCLUDED_PAGE_LIMIT"
REASON_EXCLUDED_DEPTH_LIMIT = "EXCLUDED_DEPTH_LIMIT"
REASON_EXCLUDED_INVALID_URL = "EXCLUDED_INVALID_URL"

DEFAULT_REASON_TEXT = {
    REASON_SEED_URL: "Seed URL.",
    REASON_CURRENT_PAGE_SCOPE: "Mode is CURRENT_PAGE: only the seed URL is crawled.",
    REASON_PAGINATION_SELECTOR_MATCH: "Matched the detected pagination selector.",
    REASON_PAGINATION_PATTERN_MATCH: "Matched the pagination URL pattern.",
    REASON_DATASET_PATTERN_MATCH: "Matched a dataset include pattern.",
    REASON_COLLECTION_PATTERN_MATCH: "Matched a related-list include pattern.",
    REASON_DETAIL_LINK_SELECTOR_MATCH: "Matched the detail-link selector.",
    REASON_FULL_SITE_SAME_ORIGIN: "Same-origin link in FULL_SITE scope.",
    REASON_EXCLUDED_DIFFERENT_ORIGIN: "Different origin than the seed.",
    REASON_EXCLUDED_SCOPE_MODE: "Excluded by the current crawl scope mode.",
    REASON_EXCLUDED_PATTERN: "Excluded by an exclude path pattern.",
    REASON_EXCLUDED_NAVIGATION: (
        "Skipped: looks like a navigation anchor (#, mailto, tel, javascript)."
    ),
    REASON_EXCLUDED_PAGE_LIMIT: "Excluded: page limit reached for this scope.",
    REASON_EXCLUDED_DEPTH_LIMIT: "Excluded: depth limit reached for this scope.",
    REASON_EXCLUDED_INVALID_URL: "Excluded: URL is malformed or not safe.",
}


logger = logging.getLogger(__name__)


@dataclass
class UrlDecision:
    """A single link-classification decision returned by classify_links_for_scope."""

    url: str
    normalized_url: str
    source_url: str
    depth: int
    decision: str  # "included" | "excluded"
    reason_code: str
    reason: str
    role: str | None = None
    confidence: float | None = None
    link_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "normalized_url": self.normalized_url,
            "source_url": self.source_url,
            "depth": self.depth,
            "decision": self.decision,
            "role": self.role,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "confidence": self.confidence,
            "link_text": self.link_text,
        }


# Public API


def default_crawl_scope(project: Any, analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Conservateur default scope for new projects (CURRENT_PAGE + SYSTEM_DEFAULTED).

    Prefers the evidence-based recommendation stashed in
    ``analysis["scope_recommendation"]`` by the job executor (which validated the
    suggestion against the fetched HTML). Falls back to the analysis-only
    heuristic for legacy projects whose analysis predates that field.
    """
    analysis = analysis or {}
    recommendation = analysis.get("scope_recommendation")
    if not isinstance(recommendation, dict):
        recommendation = _recommend_scope_from_analysis(analysis)
    scope = copy.deepcopy(DEFAULT_CRAWL_SCOPE)
    scope["seed_url"] = getattr(project, "url", None)
    scope["ai_recommendation"] = recommendation
    return scope


def normalize_crawl_scope(
    scope: dict[str, Any] | None,
    *,
    seed_url: str | None = None,
    page_limit: int | None = None,
) -> dict[str, Any]:
    """Coerce a possibly-missing scope into the canonical shape."""
    if not scope or not isinstance(scope, dict):
        scope = copy.deepcopy(LEGACY_COMPAT_CRAWL_SCOPE)
    out = copy.deepcopy(scope)
    out.setdefault("version", CRAWL_SCOPE_VERSION)
    out.setdefault("mode", CrawlScopeMode.CURRENT_PAGE.value)
    out.setdefault("status", "SYSTEM_DEFAULTED")
    if seed_url is not None:
        out["seed_url"] = seed_url
    if page_limit is not None:
        try:
            out["max_pages"] = max(1, min(int(page_limit), 5000))
        except (TypeError, ValueError):
            pass
    out.setdefault("max_pages", 500)
    out.setdefault("include_patterns", [])
    out.setdefault("exclude_patterns", [])
    out.setdefault("pagination", {})
    out.setdefault("link_rules", [])
    return out


def scope_requires_confirmation(scope: dict[str, Any] | None) -> bool:
    """Whether a non-CURRENT_PAGE scope needs explicit user confirmation."""
    if not scope:
        return False
    mode = scope.get("mode")
    status = scope.get("status")
    if mode == CrawlScopeMode.CURRENT_PAGE.value:
        return False
    if status == "USER_CONFIRMED":
        return False
    return True


class ScopeConfirmationError(ValueError):
    """Raised when extraction is attempted on a non-CURRENT_PAGE scope
    that has not been confirmed by the user (status != USER_CONFIRMED).

    The intended product behavior, per Phase 2.5 plan:

      * CURRENT_PAGE: no confirmation required. Extraction is always safe
        because it does not enqueue any discovered links.
      * PAGINATION / DATASET / FULL_SITE with status USER_CONFIRMED:
        extraction proceeds as the user has explicitly opted in.
      * PAGINATION / DATASET / FULL_SITE with status AI_SUGGESTED or
        SYSTEM_DEFAULTED: extraction MUST be rejected with this error
        unless the caller passes ``allow_unconfirmed=True`` (used only
        by explicit legacy-compat paths marked in code).

    The error is raised from the synchronous ``start_project_extraction``
    seam so the API can translate it into HTTP 409, and defensively from
    the background ``execute_project_extraction`` so a forgotten
    confirmation cannot silently broad-crawl.
    """

    def __init__(self, scope: dict[str, Any] | None, *, code: str = "SCOPE_NOT_CONFIRMED") -> None:
        self.scope = scope or {}
        self.code = code
        mode = self.scope.get("mode") or "UNKNOWN"
        status = self.scope.get("status") or "UNKNOWN"
        super().__init__(
            f"Crawl scope '{mode}' (status={status}) requires user confirmation "
            f"before extraction. Confirm the scope, pass allow_unconfirmed=True "
            f"for legacy compatibility, or use CURRENT_PAGE for the seed only."
        )


def assert_scope_confirmed(
    scope: dict[str, Any] | None,
    *,
    allow_unconfirmed: bool = False,
    allow_legacy_missing: bool = True,
    project_id: int | None = None,
) -> None:
    """Enforce the scope confirmation policy.

    Raises ``ScopeConfirmationError`` when the scope needs confirmation
    and either ``allow_unconfirmed`` is False or the scope is missing
    (and ``allow_legacy_missing`` is False).

    The policy is:

    * ``scope is None`` or empty dict: treated as legacy. Falls through
      if ``allow_legacy_missing`` is True. This preserves current
      behavior for projects that predate the scope field.
    * ``mode == CURRENT_PAGE``: always passes.
    * ``status == USER_CONFIRMED``: always passes.
    * Otherwise: requires ``allow_unconfirmed=True``.
    """
    if not scope:
        if allow_legacy_missing:
            return
        raise ScopeConfirmationError(
            scope,
            code="SCOPE_MISSING",
        )
    mode = scope.get("mode")
    status = scope.get("status")
    if mode == CrawlScopeMode.CURRENT_PAGE.value:
        logger.info(
            "scope.confirmation_gate_passed",
            extra={"scope_mode": mode, "project_id": project_id},
        )
        return
    if status == "USER_CONFIRMED":
        logger.info(
            "scope.confirmation_gate_passed",
            extra={
                "scope_mode": mode,
                "scope_status": status,
                "project_id": project_id,
            },
        )
        return
    if allow_unconfirmed:
        return
    logger.warning(
        "scope.confirmation_required",
        extra={
            "scope_mode": mode,
            "scope_status": status,
            "project_id": project_id,
        },
    )
    raise ScopeConfirmationError(scope)


def scope_max_pages(scope: dict[str, Any] | None) -> int:
    if not scope:
        return 500
    try:
        n = int(scope.get("max_pages") or 500)
    except (TypeError, ValueError):
        return 500
    return max(1, min(n, 5000))


def scope_max_depth(scope: dict[str, Any] | None) -> int | None:
    if not scope:
        return None
    val = scope.get("max_depth")
    if val is None:
        return None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def classify_links_for_scope(
    html: str,
    *,
    page_url: str,
    root_url: str,
    scope: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    source_depth: int = 0,
) -> list[UrlDecision]:
    """Classify every link on the page as included or excluded per the scope.

    ``source_depth`` is the crawl depth of ``page_url`` (the seed is depth 0).
    Discovered links are children at ``source_depth + 1``; when the scope has a
    positive ``max_depth`` and that child depth exceeds it, the link is excluded
    with ``REASON_EXCLUDED_DEPTH_LIMIT``. A ``max_depth`` of None or <= 0 means
    unbounded, so existing PAGINATION/DATASET/FULL_SITE scopes (which carry
    max_depth 0 from the CURRENT_PAGE default) are unaffected.
    """
    if not html or not page_url or not root_url or not scope:
        return []

    mode = scope.get("mode") or CrawlScopeMode.CURRENT_PAGE.value
    soup = BeautifulSoup(html, "lxml")
    decisions: list[UrlDecision] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            decisions.append(
                _nav_decision(href, page_url, reason_code=REASON_EXCLUDED_NAVIGATION)
            )
            continue
        try:
            normalized = normalize_url(href, page_url)
        except ValueError:
            decisions.append(
                UrlDecision(
                    url=href,
                    normalized_url=href,
                    source_url=page_url,
                    depth=0,
                    decision="excluded",
                    role=None,
                    reason_code=REASON_EXCLUDED_INVALID_URL,
                    reason=DEFAULT_REASON_TEXT[REASON_EXCLUDED_INVALID_URL],
                )
            )
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        link_text = anchor.get_text(separator=" ", strip=True) or None
        decisions.append(
            _classify_one(
                normalized,
                page_url=page_url,
                root_url=root_url,
                scope=scope,
                mode=mode,
                analysis=analysis,
                link_text=link_text,
                source_depth=source_depth,
            )
        )

    included = [d for d in decisions if d.decision == "included"]
    excluded = [d for d in decisions if d.decision == "excluded"]
    logger.info(
        "scope.classified",
        extra={
            "scope_mode": mode,
            "included_count": len(included),
            "excluded_count": len(excluded),
        },
    )
    for d in excluded:
        logger.debug(
            "scope.url_excluded",
            extra={
                "url": d.url,
                "reason_code": d.reason_code,
            },
        )
    return decisions


def discover_links_for_scope(
    html: str,
    *,
    page_url: str,
    root_url: str,
    scope: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    limit: int = 200,
    source_depth: int = 0,
) -> list[str]:
    """Return the normalized URLs the current scope would actually insert."""
    if limit <= 0 or not html:
        return []
    decisions = classify_links_for_scope(
        html,
        page_url=page_url,
        root_url=root_url,
        scope=scope,
        analysis=analysis,
        source_depth=source_depth,
    )
    return [d.normalized_url for d in decisions if d.decision == "included"][:limit]


# Internal helpers


def _nav_decision(href: str, page_url: str, *, reason_code: str) -> UrlDecision:
    return UrlDecision(
        url=href,
        normalized_url=href,
        source_url=page_url,
        depth=0,
        decision="excluded",
        role=None,
        reason_code=reason_code,
        reason=DEFAULT_REASON_TEXT[reason_code],
    )


def _glob_match(url: str, pattern: str) -> bool:
    """Glob match treating the pattern as a path-style glob (no regex).

    The convention is that ``pattern`` is matched against the URL's
    path component (not the full URL), since the same-origin check has
    already removed any cross-origin links. If the pattern does not
    start with ``/`` we treat it as a substring match anywhere in the
    URL, so callers can also pass full-URL patterns if they prefer.
    """
    if not pattern:
        return False
    if pattern.startswith("/"):
        parsed = _safe_urlparse(url)
        path = parsed.path if parsed is not None else url
        return fnmatch(path, pattern)
    return fnmatch(url, pattern)


def _collection_match(url: str, pattern: str) -> bool:
    """Segment-aware include match for COLLECTION (template fingerprinting).

    A trailing ``/*`` matches **exactly one** more path segment, so the derived
    sibling template ``/food/*`` matches ``/food/meat`` but NOT the deeper
    ``/food/meat/details`` — keeping the crawl to same-layout sibling list pages
    rather than everything under the prefix (which plain glob ``*`` would span,
    because fnmatch ``*`` crosses ``/``). Non-``/*`` patterns fall back to glob.
    """
    if not pattern:
        return False
    if pattern.endswith("/*"):
        parsed = _safe_urlparse(url)
        path = (parsed.path if parsed is not None else url) or ""
        prefix = pattern[:-2]  # "/food/*" -> "/food"
        if not path.startswith(prefix + "/"):
            return False
        remainder = path[len(prefix) + 1:].rstrip("/")
        return bool(remainder) and "/" not in remainder
    return _glob_match(url, pattern)


def _pagination_decision(
    normalized: str,
    page_url: str,
    scope: dict[str, Any],
    analysis: dict[str, Any] | None,
    role: str,
) -> UrlDecision | None:
    """Return a pagination-include decision if the URL matches the scope's pagination rule.

    The scope's pagination object is one of:
      {selector, url_pattern, estimated_pages}.
    The selector is informational only in v1 (the executor currently
    honours URL pattern + analysis-derived pagination selector). The
    function returns None when nothing matches so the caller falls back
    to other include rules.
    """
    pagination = scope.get("pagination") or {}
    if not isinstance(pagination, dict):
        pagination = {}

    url_pattern = pagination.get("url_pattern")
    if url_pattern and _glob_match(normalized, url_pattern):
        return UrlDecision(
            url=normalized,
            normalized_url=normalized,
            source_url=page_url,
            depth=0,
            decision="included",
            role=role,
            reason_code=REASON_PAGINATION_PATTERN_MATCH,
            reason=f"Matched pagination URL pattern '{url_pattern}'.",
        )

    # Heuristic: query-param OR path-based page links (e.g. ?page=2 AND
    # /catalogue/page-2.html, /page/2, /p/2). This MUST agree with
    # ``_looks_like_pagination`` used by ``recommend_scope`` — otherwise the AI
    # can recommend PAGINATION for a site whose next links the classifier then
    # refuses to enqueue, crawling only the seed (the original bug report).
    if _looks_like_pagination(normalized):
        return UrlDecision(
            url=normalized,
            normalized_url=normalized,
            source_url=page_url,
            depth=0,
            decision="included",
            role=role,
            reason_code=REASON_PAGINATION_PATTERN_MATCH,
            reason="URL looks like a page-number link.",
        )

    # Heuristic: pagination selector from the analysis (if AI suggested one).
    if analysis and pagination.get("selector"):
        sel = (analysis.get("pagination_selector") or "").strip()
        if sel and sel in normalized:
            return UrlDecision(
                url=normalized,
                normalized_url=normalized,
                source_url=page_url,
                depth=0,
                decision="included",
                role=role,
                reason_code=REASON_PAGINATION_SELECTOR_MATCH,
                reason="Matched the AI-detected pagination selector.",
            )

    return None


def _safe_urlparse(url: str) -> Any:
    from urllib.parse import urlparse

    try:
        return urlparse(url)
    except Exception:
        return None


def _classify_one(
    normalized: str,
    *,
    page_url: str,
    root_url: str,
    scope: dict[str, Any],
    mode: str,
    analysis: dict[str, Any] | None,
    link_text: str | None,
    source_depth: int = 0,
) -> UrlDecision:
    child_depth = source_depth + 1
    base = dict(
        url=normalized,
        normalized_url=normalized,
        source_url=page_url,
        depth=child_depth,
        confidence=None,
        link_text=link_text,
    )

    # 1. Different origin is always excluded.
    if not same_origin(normalized, root_url):
        return UrlDecision(
            **base,
            decision="excluded",
            reason_code=REASON_EXCLUDED_DIFFERENT_ORIGIN,
            reason=DEFAULT_REASON_TEXT[REASON_EXCLUDED_DIFFERENT_ORIGIN],
        )

    # 2. Exclude patterns win.
    for pattern in scope.get("exclude_patterns") or []:
        if _glob_match(normalized, pattern):
            return UrlDecision(
                **base,
                decision="excluded",
                reason_code=REASON_EXCLUDED_PATTERN,
                reason=f"Matched exclude pattern '{pattern}'.",
            )

    # 3. CURRENT_PAGE: never insert.
    if mode == CrawlScopeMode.CURRENT_PAGE.value:
        return UrlDecision(
            **base,
            decision="excluded",
            reason_code=REASON_CURRENT_PAGE_SCOPE,
            reason=DEFAULT_REASON_TEXT[REASON_CURRENT_PAGE_SCOPE],
        )

    # 4. Same URL as the current page: do not loop.
    try:
        if normalized == normalize_url(page_url):
            return UrlDecision(
                **base,
                decision="excluded",
                reason_code=REASON_CURRENT_PAGE_SCOPE,
                reason="Same URL as the current page.",
            )
    except ValueError:
        pass

    # 4b. Depth limit. For link-following modes, stop crawling once the child
    # depth would exceed a positive max_depth. None / <= 0 means unbounded, so
    # this only binds modes that explicitly set a positive bound (COLLECTION).
    max_depth = scope_max_depth(scope)
    if max_depth is not None and max_depth >= 1 and child_depth > max_depth:
        return UrlDecision(
            **base,
            decision="excluded",
            reason_code=REASON_EXCLUDED_DEPTH_LIMIT,
            reason=DEFAULT_REASON_TEXT[REASON_EXCLUDED_DEPTH_LIMIT],
        )

    # 5. PAGINATION.
    if mode == CrawlScopeMode.PAGINATION.value:
        decision = _pagination_decision(
            normalized, page_url, scope, analysis, role="pagination"
        )
        if decision is not None:
            return decision
        return UrlDecision(
            **base,
            decision="excluded",
            reason_code=REASON_EXCLUDED_SCOPE_MODE,
            reason="PAGINATION scope: only pagination selector/pattern matches are included.",
        )

    # 5b. COLLECTION: sibling/category list pages selected by include patterns
    # (auto-derived from the seed's dominant path prefix, e.g. "/food/*").
    # Matching is segment-aware (template fingerprinting): "/food/*" includes
    # "/food/meat" but not the deeper "/food/meat/details".
    if mode == CrawlScopeMode.COLLECTION.value:
        for pattern in scope.get("include_patterns") or []:
            if _collection_match(normalized, pattern):
                return UrlDecision(
                    **base,
                    decision="included",
                    role="collection",
                    reason_code=REASON_COLLECTION_PATTERN_MATCH,
                    reason=f"Matched related-list include pattern '{pattern}'.",
                )
        for rule in scope.get("link_rules") or []:
            if not isinstance(rule, dict):
                continue
            pattern = rule.get("pattern")
            if rule.get("role") in ("collection", "list") and pattern and _collection_match(
                normalized, pattern
            ):
                return UrlDecision(
                    **base,
                    decision="included",
                    role="collection",
                    reason_code=REASON_COLLECTION_PATTERN_MATCH,
                    reason=f"Matched related-list rule pattern '{pattern}'.",
                )
        return UrlDecision(
            **base,
            decision="excluded",
            reason_code=REASON_EXCLUDED_SCOPE_MODE,
            reason="COLLECTION scope: only related-list include patterns are included.",
        )

    # 6. DATASET.
    if mode == CrawlScopeMode.DATASET.value:
        decision = _pagination_decision(
            normalized, page_url, scope, analysis, role="dataset"
        )
        if decision is not None:
            return decision
        for pattern in scope.get("include_patterns") or []:
            if _glob_match(normalized, pattern):
                return UrlDecision(
                    **base,
                    decision="included",
                    role="dataset",
                    reason_code=REASON_DATASET_PATTERN_MATCH,
                    reason=f"Matched dataset include pattern '{pattern}'.",
                )
        for rule in scope.get("link_rules") or []:
            if not isinstance(rule, dict):
                continue
            rule_role = rule.get("role")
            pattern = rule.get("pattern")
            if rule_role in ("dataset", "detail") and pattern and _glob_match(
                normalized, pattern
            ):
                code = (
                    REASON_DETAIL_LINK_SELECTOR_MATCH
                    if rule_role == "detail"
                    else REASON_DATASET_PATTERN_MATCH
                )
                return UrlDecision(
                    **base,
                    decision="included",
                    role=rule_role,
                    reason_code=code,
                    reason=f"Matched {rule_role} rule pattern '{pattern}'.",
                )
        return UrlDecision(
            **base,
            decision="excluded",
            reason_code=REASON_EXCLUDED_SCOPE_MODE,
            reason="DATASET scope: only dataset include patterns / rules are included.",
        )

    # 7. FULL_SITE.
    if mode == CrawlScopeMode.FULL_SITE.value:
        includes = scope.get("include_patterns") or []
        if includes:
            for pattern in includes:
                if _glob_match(normalized, pattern):
                    return UrlDecision(
                        **base,
                        decision="included",
                        role="site",
                        reason_code=REASON_DATASET_PATTERN_MATCH,
                        reason=f"Matched include pattern '{pattern}'.",
                    )
            return UrlDecision(
                **base,
                decision="excluded",
                reason_code=REASON_EXCLUDED_PATTERN,
                reason="FULL_SITE scope has include patterns and this URL matched none.",
            )
        return UrlDecision(
            **base,
            decision="included",
            role="site",
            reason_code=REASON_FULL_SITE_SAME_ORIGIN,
            reason=DEFAULT_REASON_TEXT[REASON_FULL_SITE_SAME_ORIGIN],
        )

    # Unknown mode: be conservative.
    return UrlDecision(
        **base,
        decision="excluded",
        reason_code=REASON_EXCLUDED_SCOPE_MODE,
        reason=f"Unknown crawl scope mode '{mode}'.",
    )


def _recommend_scope_from_analysis(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort AI recommendation shape from a Phase-1-style analysis dict.

    Returns a dict in the shape of the ai_recommendation field, or
    None if there is no signal. This is conservative: if the analysis
    lacks a pagination_selector and lacks repeated_item_selector, no
    recommendation is emitted (the user must choose manually).
    """
    if not isinstance(analysis, dict):
        return None
    warnings: list[str] = []
    if analysis.get("pagination_selector"):
        recommended = "PAGINATION"
        confidence = 0.65
    elif analysis.get("repeated_item_selector"):
        recommended = "DATASET"
        confidence = 0.55
    else:
        recommended = "CURRENT_PAGE"
        confidence = 0.6
    return {
        "recommended_mode": recommended,
        "confidence": confidence,
        "warnings": warnings,
        "evidence": [],
    }


# Evidence-based recommendation (HTML-aware)

# A "strong cluster" of sibling list links needed before recommending COLLECTION.
COLLECTION_MIN_SIBLINGS = 3
# How many detail links justify recommending DATASET.
DATASET_MIN_DETAIL_LINKS = 3


def _same_origin_links(html: str, seed_url: str) -> list[str]:
    """Normalized, de-duplicated same-origin link URLs found on the page."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        try:
            normalized = normalize_url(href, seed_url)
        except ValueError:
            continue
        if normalized in seen:
            continue
        if not same_origin(normalized, seed_url):
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


_PAGINATION_QUERY_KEYS = {"p", "pg", "page", "pageno", "page_num", "pagenum",
                          "paged", "offset", "start", "skip"}


def _looks_like_pagination(url: str) -> bool:
    from urllib.parse import parse_qsl

    parsed = _safe_urlparse(url)
    if parsed is None:
        return False
    query = (parsed.query or "").lower()
    if query:
        for key, _value in parse_qsl(query):
            key = key.strip()
            # Explicit nav keys, or anything that starts with "page"
            # (page, page_num, pagenum, paged, ...). Avoids per_page-style
            # page-size knobs that don't imply multiple pages.
            if key in _PAGINATION_QUERY_KEYS or key.startswith("page"):
                return True
    path = (parsed.path or "").lower()
    if re.search(r"/page[/_-]?\d+", path):
        return True
    return False


def _selector_match_count(html: str, selector: str | None) -> int:
    """Number of elements a CSS selector actually matches in the HTML (0 on error)."""
    if not html or not selector:
        return 0
    try:
        return len(BeautifulSoup(html, "lxml").select(str(selector)))
    except Exception:
        return 0


def dominant_path_glob(urls: list[str], seed_url: str) -> tuple[str, int] | None:
    """Derive a sibling include glob from the seed's parent path.

    For a seed ``/food/beef-veal`` the parent prefix is ``/food`` and the glob
    is ``/food/*``; the count is how many of ``urls`` fall under that prefix.
    Returns None when the seed is top-level (no parent segment) or nothing
    matches.
    """
    seed_parsed = _safe_urlparse(seed_url)
    if seed_parsed is None:
        return None
    seed_segs = [s for s in (seed_parsed.path or "").split("/") if s]
    parent_segs = seed_segs[:-1]
    if not parent_segs:
        return None
    prefix = "/" + "/".join(parent_segs)
    glob = f"{prefix}/*"
    count = 0
    for url in urls:
        parsed = _safe_urlparse(url)
        if parsed is None:
            continue
        if (parsed.path or "").startswith(prefix + "/"):
            count += 1
    if count == 0:
        return None
    return glob, count


def dominant_prefix_glob(urls: list[str]) -> tuple[str, int] | None:
    """Most common first-path-segment glob among ``urls`` (with its count).

    Unlike :func:`dominant_path_glob` this clusters the URLs by their own
    leading path segment rather than the seed's parent, so it works when the
    excluded links live under a different prefix than the seed (e.g. a listing
    page ``/list`` linking to ``/item/1``, ``/item/2`` -> ``/item/*``). Used by
    the frontier "scope too narrow" CTA.
    """
    counts: dict[str, int] = {}
    for url in urls:
        parsed = _safe_urlparse(url)
        if parsed is None:
            continue
        segs = [s for s in (parsed.path or "").split("/") if s]
        if not segs:
            continue
        prefix = "/" + segs[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    if not counts:
        return None
    best_prefix, best_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return f"{best_prefix}/*", best_count


def recommend_scope(
    analysis: dict[str, Any] | None,
    html: str,
    seed_url: str,
) -> dict[str, Any]:
    """Evidence-based crawl-scope recommendation validated against real HTML.

    Unlike :func:`_recommend_scope_from_analysis`, this never recommends
    PAGINATION from an AI-claimed selector alone — the selector (or a URL
    heuristic) must match real anchors on the page. Returns an
    ``ai_recommendation``-shaped dict (always non-None) with an extra
    ``suggested_include_patterns`` list used by COLLECTION.
    """
    analysis = analysis if isinstance(analysis, dict) else {}
    links = _same_origin_links(html, seed_url)

    # 1. Real pagination? Selector must match an anchor, or links look paginated.
    pagination_selector = (analysis.get("pagination_selector") or "").strip()
    selector_hits = _selector_match_count(html, pagination_selector)
    heuristic_pagination = [u for u in links if _looks_like_pagination(u)]
    if selector_hits > 0 or heuristic_pagination:
        evidence = []
        if selector_hits > 0:
            evidence.append(
                f"Pagination selector matched {selector_hits} link(s) on the page."
            )
        if heuristic_pagination:
            evidence.append(
                f"{len(heuristic_pagination)} link(s) look like page-number links."
            )
        return {
            "recommended_mode": "PAGINATION",
            "confidence": 0.8,
            "warnings": [],
            "evidence": evidence,
            "suggested_include_patterns": [],
        }

    # 2. Strong cluster of sibling list pages? -> COLLECTION with derived glob.
    dominant = dominant_path_glob(links, seed_url)
    if dominant is not None and dominant[1] >= COLLECTION_MIN_SIBLINGS:
        glob, count = dominant
        return {
            "recommended_mode": "COLLECTION",
            "confidence": 0.7,
            "warnings": [],
            "evidence": [
                f"{count} sibling list link(s) under '{glob}'."
            ],
            "suggested_include_patterns": [glob],
        }

    # 3. Detail links present? -> DATASET (listing + detail pages).
    detail_hits = _selector_match_count(html, analysis.get("detail_link_selector"))
    if detail_hits >= DATASET_MIN_DETAIL_LINKS:
        return {
            "recommended_mode": "DATASET",
            "confidence": 0.6,
            "warnings": [],
            "evidence": [
                f"Detail-link selector matched {detail_hits} link(s) on the page."
            ],
            "suggested_include_patterns": [],
        }

    # 4. Nothing actionable -> stay on the seed page.
    return {
        "recommended_mode": "CURRENT_PAGE",
        "confidence": 0.6,
        "warnings": [],
        "evidence": [],
        "suggested_include_patterns": [],
    }
