"""Frontier preview service (Workstream B, behavior layer).

Generates a pre-extraction preview of the crawl frontier. The preview
reuses the same scope-aware classifier as ``crawl_scope`` so preview
and extraction agree on what is included and excluded.

v1 is bounded: it samples the seed page only and uses the same
heuristic link discovery the executor uses. Multi-page sampling and
LLM-assisted link classification are deferred.

The preview row counts are stored as small samples (default 100 each)
so the row never grows unbounded. The estimated page count is the
``spec.crawl_scope.max_pages`` clamped to ``MAX_PAGES_PER_JOB``.

Persistence design
------------------

There are exactly two entry points and they are not interchangeable:

* :func:`build_frontier_preview` returns a fully-populated
  ``FrontierPreview`` SQLAlchemy object that is **not** attached to any
  session. Use this in tests, in dry-runs, or anywhere you need the
  preview payload without committing. Callers must NOT add this to a
  session themselves unless they really mean to; the public
  ``create_frontier_preview`` already does the persistence.

* :func:`create_frontier_preview` calls ``build_frontier_preview`` and
  then ``db.add(row)``, ``await db.flush()``, ``await db.refresh(row)``
  so the returned object is a real, persisted, queryable ``FrontierPreview``
  row. ``latest_frontier_preview`` will find it.

Splitting these two functions makes the persistence step explicit and
prevents the Step 3 API from accidentally returning a non-persisted
object.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bs4 import BeautifulSoup

from app.models.job import FrontierPreview, Project
from app.services.crawl_scope import (
    REASON_CURRENT_PAGE_SCOPE,
    REASON_EXCLUDED_SCOPE_MODE,
    classify_links_for_scope,
    dominant_path_glob,
    dominant_prefix_glob,
    normalize_crawl_scope,
    scope_max_pages,
)
from app.core.log_context import set_task_context
from app.services.extraction_spec_service import latest_spec
from app.services.fetcher import fetch_url
from app.services.url_normalizer import normalize_url
from app.services.url_validator import URLValidationError, validate_url

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_LIMIT = 100
SCOPE_EXCLUSION_THRESHOLD = 10


def _scope_hash(scope: dict[str, Any]) -> str:
    payload = json.dumps(scope, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_frontier_preview_from_fetch(
    project: Project,
    spec,
    html: str,
    *,
    max_urls: int = DEFAULT_SAMPLE_LIMIT,
) -> FrontierPreview | None:
    """Build (but do not persist) a frontier preview given fetched HTML.

    Splitting the build from the fetch keeps this function pure and
    testable: tests can pass inline HTML strings without monkey-patching
    the fetcher. It is a synchronous, pure-Python helper; the only
    async work in this module is the real fetch done by
    :func:`create_frontier_preview`.
    """
    if spec is None:
        return None
    seed = project.normalized_url or project.url
    if not seed:
        return None
    try:
        seed_validated = validate_url(seed)
    except URLValidationError:
        return None
    scope = normalize_crawl_scope(
        spec.crawl_scope,
        seed_url=seed_validated,
        page_limit=getattr(spec, "page_limit", None),
    )

    decisions = classify_links_for_scope(
        html,
        page_url=seed_validated,
        root_url=seed_validated,
        scope=scope,
        analysis=project.analysis if isinstance(project.analysis, dict) else None,
    )

    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    unrelated_same_origin_count = 0
    # All same-origin links the scope dropped (uncapped, for clustering the CTA).
    excluded_same_origin_urls: list[str] = []
    for d in decisions:
        if d.decision == "included":
            if len(included) < max_urls:
                included.append(d.to_dict())
        else:
            # Count same-origin links that the current scope dropped.
            # That is the count the user actually needs to see: same-origin
            # but not selected by the chosen mode (e.g. category links
            # excluded under PAGINATION, or any link under CURRENT_PAGE).
            if d.reason_code in (REASON_EXCLUDED_SCOPE_MODE, REASON_CURRENT_PAGE_SCOPE):
                unrelated_same_origin_count += 1
                excluded_same_origin_urls.append(d.normalized_url)
            if len(excluded) < max_urls:
                excluded.append(d.to_dict())

    warnings: list[dict[str, Any]] = []
    if unrelated_same_origin_count >= SCOPE_EXCLUSION_THRESHOLD:
        warnings.append(
            {
                "code": "FRONTIER_HAS_MANY_EXCLUSIONS",
                "count": unrelated_same_origin_count,
                "message": (
                    f"{unrelated_same_origin_count} same-origin links were "
                    f"excluded by the current crawl scope mode."
                ),
            }
        )

    # Actionable CTA: the chosen scope will only crawl the seed page, yet the
    # page links to a strong cluster of same-origin pages. Offer a one-click
    # broaden to COLLECTION (sibling/category lists) or DATASET (per-item detail
    # pages). This subsumes the old PAGINATION-only SCOPE_NO_MATCHING_LINKS hint
    # and works for every narrow scope (CURRENT_PAGE, PAGINATION-with-no-pages).
    if len(included) == 0 and unrelated_same_origin_count >= SCOPE_EXCLUSION_THRESHOLD:
        sibling_dominant = dominant_path_glob(excluded_same_origin_urls, seed_validated)
        dominant = sibling_dominant or dominant_prefix_glob(excluded_same_origin_urls)
        if dominant is not None:
            glob, count = dominant
            analysis = project.analysis if isinstance(project.analysis, dict) else {}
            detail_sel = analysis.get("detail_link_selector")
            detail_matches = 0
            if detail_sel:
                try:
                    detail_matches = len(
                        BeautifulSoup(html, "lxml").select(str(detail_sel))
                    )
                except Exception:
                    detail_matches = 0
            if sibling_dominant is None and detail_matches >= 3:
                suggested_mode = "DATASET"
                mode_label = "Listing + detail pages"
            else:
                suggested_mode = "COLLECTION"
                mode_label = "Related list pages"
            warnings.append(
                {
                    "code": "SCOPE_TOO_NARROW",
                    "suggested_mode": suggested_mode,
                    "suggested_include_patterns": [glob],
                    "count": count,
                    "message": (
                        "This scope will only crawl the seed page, but it links "
                        f"to {count} related page(s) under '{glob}'. Switch to "
                        f"'{mode_label}' to crawl them."
                    ),
                }
            )

    seed_decision = {
        "url": seed,
        "normalized_url": normalize_url(seed),
        "source_url": None,
        "depth": 0,
        "decision": "included",
        "role": "seed",
        "reason_code": "SEED_URL",
        "reason": "Seed URL.",
        "confidence": None,
        "link_text": None,
    }
    if not included:
        included.append(seed_decision)
    elif included[0].get("normalized_url") != seed_decision["normalized_url"]:
        included.insert(0, seed_decision)

    return FrontierPreview(
        project_id=project.id,
        spec_id=spec.id,
        scope_hash=_scope_hash(scope),
        included_urls=included,
        excluded_urls=excluded,
        estimated_page_count=scope_max_pages(scope),
        warnings=warnings,
        quality_summary={
            "included_count": len(included),
            "excluded_count": len(excluded),
            "unrelated_same_origin_count": unrelated_same_origin_count,
            "source": "seed_page_frontier_preview",
        },
    )


async def create_frontier_preview(
    db: AsyncSession,
    project: Project,
    *,
    max_urls: int = DEFAULT_SAMPLE_LIMIT,
) -> FrontierPreview | None:
    """Persist a frontier preview for the project's current spec.

    The function fetches the seed page using the project's render
    mode, runs the scope-aware classifier, and writes the resulting
    ``FrontierPreview`` row to the database. Returns the persisted row
    (so callers can use ``.id`` immediately) or None if the spec is
    missing or the seed URL cannot be fetched safely.

    This is the only public entry point that mutates the database. The
    builder functions above are read-only.
    """
    spec = await latest_spec(db, project.id)
    if spec is None:
        return None

    set_task_context(
        project_id=project.id,
        user_id=project.user_id,
    )

    seed = project.normalized_url or project.url
    if not seed:
        return None
    try:
        seed_validated = validate_url(seed)
    except URLValidationError:
        return None
    scope = normalize_crawl_scope(
        spec.crawl_scope,
        seed_url=seed_validated,
        page_limit=getattr(spec, "page_limit", None),
    )
    if spec.crawl_scope != scope:
        spec.crawl_scope = scope

    logger.debug(
        "frontier.fetch_started",
        extra={"project_id": project.id, "url": seed_validated},
    )
    try:
        fetch = await fetch_url(
            seed_validated, project.render_mode.value
        )
    except Exception as exc:
        logger.error(
            "frontier.fetch_failed",
            extra={
                "project_id": project.id,
                "url": seed_validated,
                "error_type": type(exc).__name__,
            },
        )
        fetch = None

    html = fetch.html if fetch is not None else ""
    if not html:
        preview = FrontierPreview(
            project_id=project.id,
            spec_id=spec.id,
            scope_hash=_scope_hash(scope),
            included_urls=[],
            excluded_urls=[],
            estimated_page_count=scope_max_pages(scope),
            warnings=[
                {
                    "code": "SEED_FETCH_FAILED",
                    "message": "Could not fetch the seed URL; preview is empty.",
                }
            ],
            quality_summary={
                "included_count": 0,
                "excluded_count": 0,
                "unrelated_same_origin_count": 0,
                "source": "seed_page_frontier_preview",
            },
        )
    else:
        preview = build_frontier_preview_from_fetch(
            project, spec, html, max_urls=max_urls
        )
        if preview is None:
            return None
        scope_mode = scope.get("mode", "CURRENT_PAGE")
        included_count = len(preview.included_urls or [])
        excluded_count = len(preview.excluded_urls or [])
        logger.info(
            "frontier.preview_built",
            extra={
                "project_id": project.id,
                "scope_mode": scope_mode,
                "included_count": included_count,
                "excluded_count": excluded_count,
            },
        )
        total_decisions = included_count + excluded_count
        if total_decisions > 0 and excluded_count / total_decisions >= 0.8:
            logger.warning(
                "frontier.high_exclusion_rate",
                extra={
                    "project_id": project.id,
                    "excluded_pct": round(
                        excluded_count / total_decisions * 100, 1
                    ),
                },
            )

    db.add(preview)
    await db.flush()
    await db.refresh(preview)
    return preview


async def latest_frontier_preview(db: AsyncSession, project_id: int) -> FrontierPreview | None:
    result = await db.execute(
        select(FrontierPreview)
        .where(FrontierPreview.project_id == project_id)
        .order_by(FrontierPreview.created_at.desc(), FrontierPreview.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


__all__ = [
    "DEFAULT_SAMPLE_LIMIT",
    "SCOPE_EXCLUSION_THRESHOLD",
    "build_frontier_preview_from_fetch",
    "create_frontier_preview",
    "latest_frontier_preview",
]
