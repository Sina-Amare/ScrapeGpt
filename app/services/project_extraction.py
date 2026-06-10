"""Project extraction executor: crawl same-site pages and extract real records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.log_context import set_page_context, set_task_context
from app.db.database import async_session_factory
from app.models.job import (
    CrawlPage,
    CrawlPageState,
    Export,
    ExtractedRecord,
    ExtractionSpec,
    Project,
    ProjectState,
)
from app.services.crawl_scope import (
    CrawlScopeMode,
    ScopeConfirmationError,
    assert_scope_confirmed,
    discover_links_for_scope,
    scope_max_pages,
)
from app.services.extractor import extract_records_from_html
from app.services.fetcher import FetchError, fetch_url
from app.services.robots_service import RobotsResult, check_robots
from app.services.url_normalizer import discover_same_site_links, normalize_url
from app.services.url_validator import URLValidationError, validate_url
from app.services.extraction_quality import compute_extraction_quality


logger = logging.getLogger(__name__)


def _spec_hash(spec: ExtractionSpec) -> str:
    payload = {
        "fields": spec.fields or [],
        "content_config": spec.content_config or {},
        "url_patterns": spec.url_patterns or [],
        "page_limit": spec.page_limit,
        "export_format": spec.export_format,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


async def start_project_extraction(
    db: AsyncSession,
    project: Project,
    spec: ExtractionSpec,
    *,
    allow_unconfirmed: bool = False,
) -> None:
    """Prepare extraction state and queue the seed page.

    Enforces the scope confirmation policy. By default, non-CURRENT_PAGE
    scopes with status != USER_CONFIRMED are rejected with
    ``ScopeConfirmationError``. The API layer is expected to translate
    that error into HTTP 409. The background executor and tests may pass
    ``allow_unconfirmed=True`` for explicit legacy-compat paths.
    """
    assert_scope_confirmed(
        spec.crawl_scope,
        allow_unconfirmed=allow_unconfirmed,
        allow_legacy_missing=True,
        project_id=project.id,
    )
    project.transition_to(ProjectState.DISCOVERING)
    project.error = None
    project.error_code = None

    await db.execute(delete(ExtractedRecord).where(ExtractedRecord.project_id == project.id))
    await db.execute(delete(CrawlPage).where(CrawlPage.project_id == project.id))
    await db.execute(delete(Export).where(Export.project_id == project.id))

    normalized = normalize_url(project.normalized_url or project.url)
    db.add(
        CrawlPage(
            project_id=project.id,
            url=project.url,
            normalized_url=normalized,
            state=CrawlPageState.PENDING,
            depth=0,
        )
    )
    await db.flush()


def select_links_to_enqueue(
    *,
    html: str,
    page_url: str,
    root_url: str,
    scope: dict[str, Any] | None,
    legacy_patterns: list[str] | None = None,
    analysis: dict[str, Any] | None = None,
    remaining_slots: int,
) -> list[str]:
    """Decide which discovered links to enqueue as the next crawl batch.

    This is the small extraction-service seam exercised by the
    integration tests. It is also the function called by
    ``execute_project_extraction`` for every fetched page. It returns
    the normalized URLs that should be enqueued, never the full
    UrlDecision list. ``remaining_slots`` is the maximum number of
    URLs to return. When ``scope`` is missing or has no mode, the
    legacy same-site discoverer is used.
    """
    if remaining_slots <= 0 or not html:
        return []
    scope_mode = scope.get("mode") if isinstance(scope, dict) else None
    if scope_mode:
        links = discover_links_for_scope(
            html,
            page_url=page_url,
            root_url=root_url,
            scope=scope,
            analysis=analysis,
            limit=remaining_slots,
        )
    else:
        links = discover_same_site_links(
            html,
            page_url=page_url,
            root_url=root_url,
            patterns=legacy_patterns or [],
            limit=remaining_slots,
        )
    if scope_mode == CrawlScopeMode.CURRENT_PAGE.value:
        links = []
    return links


async def _project_was_canceled(db: AsyncSession, project_id: int) -> bool:
    state = await db.scalar(select(Project.state).where(Project.id == project_id))
    return state == ProjectState.CANCELED


async def _pending_page(db: AsyncSession, project_id: int) -> CrawlPage | None:
    result = await db.execute(
        select(CrawlPage)
        .where(CrawlPage.project_id == project_id, CrawlPage.state == CrawlPageState.PENDING)
        .order_by(CrawlPage.depth.asc(), CrawlPage.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _crawl_page_count(db: AsyncSession, project_id: int) -> int:
    value = await db.scalar(select(func.count(CrawlPage.id)).where(CrawlPage.project_id == project_id))
    return int(value or 0)


async def _insert_discovered_pages(
    db: AsyncSession,
    *,
    project_id: int,
    urls: list[str],
    depth: int,
    remaining_slots: int,
) -> int:
    if remaining_slots <= 0 or not urls:
        return 0
    rows = [
        {
            "project_id": project_id,
            "url": url,
            "normalized_url": url,
            "state": CrawlPageState.PENDING.value,
            "depth": depth,
        }
        for url in urls[:remaining_slots]
    ]
    statement = insert(CrawlPage).values(rows).on_conflict_do_nothing(
        constraint="uq_crawl_pages_project_url"
    )
    result = await db.execute(statement)
    return int(result.rowcount or 0)


async def _mark_project_failed(db: AsyncSession, project: Project, message: str, code: str) -> None:
    if project.state != ProjectState.FAILED and project.can_transition_to(ProjectState.FAILED):
        project.transition_to(ProjectState.FAILED)
    else:
        project.state = ProjectState.FAILED
    project.error = message
    project.error_code = code
    await db.commit()


async def execute_project_extraction(project_id: int, spec_id: int) -> None:
    """Run the crawl/extract pipeline as a background task."""
    logger.info(
        "project_extraction.started",
        extra={"project_id": project_id, "spec_id": spec_id},
    )
    async with async_session_factory() as db:
        project = await db.get(Project, project_id)
        spec = await db.get(ExtractionSpec, spec_id)
        if not project or not spec or spec.project_id != project_id:
            logger.error(
                "project_extraction.missing_state",
                extra={
                    "project_id": project_id,
                    "spec_id": spec_id,
                },
            )
            return

        set_task_context(
            project_id=project_id,
            user_id=project.user_id,
        )

        try:
            validated_seed = validate_url(project.normalized_url or project.url)
        except URLValidationError as exc:
            await _mark_project_failed(db, project, str(exc), exc.reason.value)
            return

        try:
            # Defensive confirmation gate. ``start_project_extraction``
            # already enforces this synchronously; this catch-all keeps
            # a forgotten confirmation from silently broad-crawling if
            # the executor is ever invoked directly. A scope that is
            # missing (legacy) still passes here so existing data is
            # not stranded; the API never starts such a scope anyway.
            assert_scope_confirmed(
                spec.crawl_scope,
                allow_unconfirmed=False,
                allow_legacy_missing=True,
                project_id=project_id,
            )

            if project.state == ProjectState.DISCOVERING:
                project.transition_to(ProjectState.EXTRACTING)
                await db.commit()

            processed_pages = 0
            total_records = 0
            scope = spec.crawl_scope or {}
            scope_mode = scope.get("mode") if scope else None
            page_limit = min(spec.page_limit, settings.MAX_PAGES_PER_JOB)
            if isinstance(scope, dict) and scope_mode:
                try:
                    scope_max = scope_max_pages(scope)
                    if scope_max < page_limit:
                        page_limit = scope_max
                except Exception as exc:
                    logger.error(
                        "extraction.scope_max_pages_failed",
                        extra={
                            "project_id": project_id,
                            "error_type": type(exc).__name__,
                        },
                    )

            while processed_pages < page_limit:
                if await _project_was_canceled(db, project_id):
                    logger.info("project_extraction.canceled", extra={"project_id": project_id})
                    return

                page = await _pending_page(db, project_id)
                if page is None:
                    break

                page.state = CrawlPageState.FETCHING
                page.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                await db.commit()
                set_page_context(page_id=page.id)

                try:
                    validated_url = validate_url(page.normalized_url)
                    robots = await check_robots(validated_url)
                    if robots.result == RobotsResult.BLOCKED:
                        page.state = CrawlPageState.BLOCKED
                        page.block_reason = "ROBOTS_BLOCKED"
                        page.error = robots.reason
                        await db.commit()
                        logger.warning(
                            "extraction.page_robots_blocked",
                            extra={
                                "project_id": project_id,
                                "page_id": page.id,
                                "url": validated_url,
                            },
                        )
                        processed_pages += 1
                        continue
                    if robots.result == RobotsResult.UNAVAILABLE:
                        page.state = CrawlPageState.BLOCKED
                        page.block_reason = "ROBOTS_UNAVAILABLE"
                        page.error = robots.reason
                        await db.commit()
                        logger.warning(
                            "extraction.page_robots_blocked",
                            extra={
                                "project_id": project_id,
                                "page_id": page.id,
                                "url": validated_url,
                                "reason": "robots_unavailable",
                            },
                        )
                        processed_pages += 1
                        continue

                    fetched = await fetch_url(validated_url, project.render_mode.value)
                    page.url = fetched.final_url
                    page.normalized_url = normalize_url(fetched.final_url)

                    current_count = await _crawl_page_count(db, project_id)
                    remaining = max(0, page_limit - current_count)
                    if isinstance(scope, dict) and scope_mode:
                        links = select_links_to_enqueue(
                            html=fetched.html,
                            page_url=fetched.final_url,
                            root_url=validated_seed,
                            scope=scope,
                            legacy_patterns=spec.url_patterns or [],
                            analysis=project.analysis if isinstance(project.analysis, dict) else None,
                            remaining_slots=remaining,
                        )
                    else:
                        # Legacy: no scope, fall back to same-site BFS.
                        links = discover_same_site_links(
                            fetched.html,
                            page_url=fetched.final_url,
                            root_url=validated_seed,
                            patterns=spec.url_patterns or [],
                            limit=remaining,
                        )

                    await _insert_discovered_pages(
                        db,
                        project_id=project_id,
                        urls=links,
                        depth=page.depth + 1,
                        remaining_slots=remaining,
                    )

                    extracted = extract_records_from_html(
                        fetched.html,
                        source_url=fetched.final_url,
                        project=project,
                        spec=spec,
                    )
                    for item in extracted:
                        db.add(
                            ExtractedRecord(
                                project_id=project.id,
                                page_id=page.id,
                                source_url=fetched.final_url,
                                raw_data=item.raw_data,
                                normalized_data=item.normalized_data,
                                warnings=item.warnings,
                            )
                        )
                    total_records += len(extracted)
                    logger.debug(
                        "extraction.records_extracted",
                        extra={
                            "project_id": project_id,
                            "page_id": page.id,
                            "record_count": len(extracted),
                            "warnings_count": sum(
                                len(item.warnings or []) for item in extracted
                            ),
                        },
                    )
                    page.state = CrawlPageState.EXTRACTED
                    page.error = None
                    page.block_reason = None
                    page.lease_expires_at = None
                    await db.commit()

                except (FetchError, URLValidationError) as exc:
                    page.state = CrawlPageState.FAILED
                    page.retry_count += 1
                    page.error = str(exc)
                    page.lease_expires_at = None
                    await db.commit()
                    logger.error(
                        "extraction.page_failed",
                        extra={
                            "project_id": project_id,
                            "page_id": page.id,
                            "url": page.normalized_url,
                            "error_type": type(exc).__name__,
                        },
                    )

                processed_pages += 1
                if settings.MIN_CRAWL_DELAY_MS:
                    await asyncio.sleep(settings.MIN_CRAWL_DELAY_MS / 1000)

            project = await db.get(Project, project_id)
            if not project or project.state == ProjectState.CANCELED:
                return
            if project.state == ProjectState.EXTRACTING:
                project.transition_to(ProjectState.EXPORTING)
            elif project.can_transition_to(ProjectState.EXPORTING):
                project.transition_to(ProjectState.EXPORTING)
            else:
                project.state = ProjectState.EXPORTING

            try:
                records = (
                    await db.execute(
                        select(ExtractedRecord).where(
                            ExtractedRecord.project_id == project_id
                        )
                    )
                ).scalars().all()
                pages_total = processed_pages
                pages_failed = sum(
                    1
                    for page_row in (
                        await db.execute(
                            select(CrawlPage).where(CrawlPage.project_id == project_id)
                        )
                    ).scalars()
                    if page_row.state == CrawlPageState.FAILED
                )
                spec.quality_summary = compute_extraction_quality(
                    records,
                    spec,
                    pages_attempted=pages_total,
                    pages_failed=pages_failed,
                )
                quality_label = (
                    spec.quality_summary.get("overall_label")
                    if isinstance(spec.quality_summary, dict)
                    else None
                )
                field_count = len(spec.fields or [])
                logger.info(
                    "extraction.quality_computed",
                    extra={
                        "project_id": project_id,
                        "quality_label": quality_label,
                        "field_count": field_count,
                    },
                )
            except Exception as exc:
                logger.error(
                    "extraction.quality_computation_failed",
                    extra={
                        "project_id": project_id,
                        "error_type": type(exc).__name__,
                    },
                )
                spec.quality_summary = {}

            db.add(
                Export(
                    project_id=project.id,
                    format=spec.export_format or "csv",
                    record_count=total_records,
                    spec_hash=_spec_hash(spec),
                )
            )
            project.transition_to(ProjectState.COMPLETED)
            await db.commit()

            logger.info(
                "project_extraction.completed",
                extra={"project_id": project_id, "records": total_records, "pages": processed_pages},
            )
        except ScopeConfirmationError as exc:
            logger.exception("project_extraction.scope_unconfirmed", extra={"project_id": project_id, "error": str(exc)})
            project = await db.get(Project, project_id)
            if project and project.state != ProjectState.CANCELED:
                await _mark_project_failed(db, project, str(exc), exc.code)
        except Exception as exc:
            logger.exception("project_extraction.failed", extra={"project_id": project_id, "error": str(exc)})
            project = await db.get(Project, project_id)
            if project and project.state != ProjectState.CANCELED:
                await _mark_project_failed(db, project, f"Extraction failed: {exc}", "EXTRACTION_FAILED")


async def list_records(
    db: AsyncSession,
    project_id: int,
    skip: int,
    limit: int,
) -> list[ExtractedRecord]:
    result = await db.execute(
        select(ExtractedRecord)
        .where(ExtractedRecord.project_id == project_id)
        .order_by(ExtractedRecord.id.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_records(db: AsyncSession, project_id: int) -> int:
    result = await db.scalar(
        select(func.count(ExtractedRecord.id)).where(ExtractedRecord.project_id == project_id)
    )
    return int(result or 0)
