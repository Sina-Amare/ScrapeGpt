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
    ExtractionMode,
    ExtractionSpec,
    Project,
    ProjectState,
)
from app.services.anti_bot import CHALLENGE_MESSAGES, anti_bot_challenge_reason
from app.services.dom_summary import assess_html_quality
from app.services.session_service import get_cookies_for_session
from app.services.crawl_scope import (
    CrawlScopeMode,
    ScopeConfirmationError,
    assert_scope_confirmed,
    discover_links_for_scope,
    scope_max_pages,
)
from app.services.fetcher import (
    FetchError,
    apply_interactions_and_capture,
    fetch_url,
)
from app.services.interaction_extraction import extract_records_with_variants
from app.services.interaction_profile import InteractionError
from app.services.project_events import record_project_event
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
        # crawl_scope changes the crawl frontier (e.g. CURRENT_PAGE vs
        # COLLECTION) and interaction_profile changes which variants are
        # extracted, so both are part of the spec shape an export came from.
        "crawl_scope": spec.crawl_scope or {},
        "interaction_profile": getattr(spec, "interaction_profile", None) or {},
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
    source_depth: int = 0,
) -> list[str]:
    """Decide which discovered links to enqueue as the next crawl batch.

    This is the small extraction-service seam exercised by the
    integration tests. It is also the function called by
    ``execute_project_extraction`` for every fetched page. It returns
    the normalized URLs that should be enqueued, never the full
    UrlDecision list. ``remaining_slots`` is the maximum number of
    URLs to return. ``source_depth`` is the depth of ``page_url`` so the
    scope classifier can enforce a positive ``max_depth`` bound. When
    ``scope`` is missing or has no mode, the legacy same-site discoverer
    is used.
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
            source_depth=source_depth,
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


async def _mark_project_failed(
    db: AsyncSession, project: Project, message: str, code: str
) -> None:
    """Transition project to FAILED, enforcing the state machine.

    If the project is already FAILED, just update the error fields.
    If the transition is not allowed by the state machine, log an
    error and force the transition as a defensive fallback — this
    should not happen with the current transition table but must
    not leave the project in an active state.
    """
    if project.state == ProjectState.FAILED:
        # Already failed — just update error details.
        pass
    elif project.can_transition_to(ProjectState.FAILED):
        project.transition_to(ProjectState.FAILED)
    else:
        logger.error(
            "extraction.cannot_transition_to_failed",
            extra={
                "project_id": project.id,
                "current_state": project.state.value,
            },
        )
        project.state = ProjectState.FAILED
    project.error = message
    project.error_code = code
    await db.commit()
    await record_project_event(
        project.id,
        project.user_id,
        "extraction.failed",
        level="error",
        message=message,
        metadata={"error_code": code},
    )


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
                await record_project_event(
                    project_id,
                    project.user_id,
                    "extraction.started",
                    message="Extraction started.",
                )

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

            # Load session cookies once for the whole crawl.
            session_cookies: list[dict] | None = None
            if project.browser_session_id is not None:
                session_cookies = await get_cookies_for_session(
                    db,
                    project.browser_session_id,
                    owner_user_id=project.user_id,
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

                    effective_render_mode = project.render_mode.value
                    if (
                        effective_render_mode == "AUTO"
                        and isinstance(project.fetch_metadata, dict)
                        and project.fetch_metadata.get("render_mode_used") == "BROWSER"
                    ):
                        effective_render_mode = "BROWSER"
                    fetched = await fetch_url(
                        validated_url,
                        effective_render_mode,
                        browser_session_cookies=session_cookies,
                    )
                    challenge_reason = anti_bot_challenge_reason(fetched.html, fetched.final_url)
                    if challenge_reason:
                        page.state = CrawlPageState.BLOCKED
                        page.block_reason = "ANTI_BOT_CHALLENGE"
                        page.error = CHALLENGE_MESSAGES.get(
                            challenge_reason,
                            f"Anti-bot challenge detected: {challenge_reason}",
                        )
                        page.lease_expires_at = None
                        await db.commit()
                        logger.warning(
                            "extraction.page_anti_bot_blocked",
                            extra={
                                "project_id": project_id,
                                "page_id": page.id,
                                "url": fetched.final_url,
                                "reason": challenge_reason,
                            },
                        )
                        processed_pages += 1
                        continue

                    # Undecodable/garbled body — fail this page with a precise
                    # cause instead of "extracting" 0 records from garbage.
                    if assess_html_quality(fetched.html).is_binary:
                        page.state = CrawlPageState.FAILED
                        page.block_reason = "PAGE_DECODE_FAILED"
                        page.error = (
                            "Page could not be decoded "
                            "(unsupported compression or encoding)."
                        )
                        page.lease_expires_at = None
                        await db.commit()
                        logger.warning(
                            "extraction.page_decode_failed",
                            extra={
                                "project_id": project_id,
                                "page_id": page.id,
                                "url": fetched.final_url,
                            },
                        )
                        processed_pages += 1
                        continue

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
                            source_depth=page.depth,
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

                    async def _fetch_variant_htmls(
                        recipes: dict[str, list[dict[str, Any]]],
                        _url: str = fetched.final_url,
                    ) -> dict[str, str]:
                        try:
                            return await apply_interactions_and_capture(
                                _url, recipes, cookies=session_cookies
                            )
                        except FetchError as exc:
                            if exc.error_code == "BROWSER_UNAVAILABLE":
                                raise InteractionError(
                                    "A browser backend is required to extract the "
                                    "selected interactive variant(s).",
                                    code="INTERACTION_BROWSER_REQUIRED",
                                ) from exc
                            raise

                    async def _fetch_variant_url_htmls(
                        urls: dict[str, str],
                        _render: str = effective_render_mode,
                    ) -> dict[str, str]:
                        out: dict[str, str] = {}
                        for vid, vurl in urls.items():
                            v = validate_url(vurl)
                            vf = await fetch_url(
                                v, _render, browser_session_cookies=session_cookies
                            )
                            out[vid] = vf.html
                        return out

                    extracted, variant_warnings = await extract_records_with_variants(
                        base_html=fetched.html,
                        source_url=fetched.final_url,
                        project=project,
                        spec=spec,
                        max_records=settings.MAX_RECORDS_PER_PAGE,
                        fetch_variant_htmls=_fetch_variant_htmls,
                        fetch_variant_url_htmls=_fetch_variant_url_htmls,
                    )
                    for w in variant_warnings:
                        logger.info(
                            "extraction.variant_warning",
                            extra={"project_id": project_id, "page_id": page.id, "warning": w},
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
                    page.lease_expires_at = None
                    if extracted:
                        page.state = CrawlPageState.EXTRACTED
                        page.error = None
                        page.block_reason = None
                    else:
                        # Fetched fine, but selectors matched nothing. Mark it
                        # non-success (FAILED + reason) so progress and the UI
                        # don't show a misleading "extracted" page. The project
                        # still ends as NO_RECORDS_EXTRACTED (not ALL_PAGES_FAILED)
                        # because the post-loop counts these as fetched-OK pages.
                        page.state = CrawlPageState.FAILED
                        page.block_reason = "SELECTOR_ZERO_MATCH"
                        page.error = "Selectors matched no elements on this page."
                    await db.commit()

                except InteractionError as exc:
                    # Spec-level variant config problem (browser missing / too many
                    # combinations). Every page would fail identically, so abort the
                    # whole run with the precise code rather than failing page-by-page.
                    page.state = CrawlPageState.FAILED
                    page.error = str(exc)
                    page.block_reason = exc.code
                    page.lease_expires_at = None
                    await db.commit()
                    await _mark_project_failed(db, project, str(exc), exc.code)
                    logger.warning(
                        "extraction.interaction_failed",
                        extra={"project_id": project_id, "error_code": exc.code},
                    )
                    return

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

            # Post-loop completion check: if no pages were successfully
            # extracted, the project should fail rather than complete
            # with zero results. This covers the case where every page
            # hit a fetch error, URL validation error, or robots block.
            # Per-page failure reasons are already stored on each
            # CrawlPage row for debugging.
            pages_extracted = await db.scalar(
                select(func.count(CrawlPage.id)).where(
                    CrawlPage.project_id == project_id,
                    CrawlPage.state == CrawlPageState.EXTRACTED,
                )
            )
            pages_extracted = int(pages_extracted or 0)
            # Pages that fetched fine but whose selectors matched nothing are
            # FAILED+SELECTOR_ZERO_MATCH. They still count as a successful fetch,
            # so the project fails as NO_RECORDS_EXTRACTED (selectors), not
            # ALL_PAGES_FAILED (couldn't fetch anything).
            pages_zero_match = int(await db.scalar(
                select(func.count(CrawlPage.id)).where(
                    CrawlPage.project_id == project_id,
                    CrawlPage.state == CrawlPageState.FAILED,
                    CrawlPage.block_reason == "SELECTOR_ZERO_MATCH",
                )
            ) or 0)
            pages_fetched_ok = pages_extracted + pages_zero_match

            if pages_fetched_ok == 0 and total_records == 0:
                logger.warning(
                    "extraction.all_pages_failed",
                    extra={
                        "project_id": project_id,
                        "pages_attempted": processed_pages,
                    },
                )
                project = await db.get(Project, project_id)
                if project and not project.is_terminal:
                    # Distinguish bot-protection blocks from generic failures
                    # so the UI can offer a session-based retry.
                    blocked_rows = (await db.execute(
                        select(CrawlPage.block_reason).where(
                            CrawlPage.project_id == project_id,
                            CrawlPage.state == CrawlPageState.BLOCKED,
                        )
                    )).scalars().all()
                    failed_count = int(await db.scalar(
                        select(func.count(CrawlPage.id)).where(
                            CrawlPage.project_id == project_id,
                            CrawlPage.state == CrawlPageState.FAILED,
                        )
                    ) or 0)
                    bot_blocked = [
                        r for r in blocked_rows
                        if r == "ANTI_BOT_CHALLENGE"
                    ]
                    all_bot = (
                        bot_blocked
                        and len(bot_blocked) == len(blocked_rows)
                        and failed_count == 0
                    )
                    if all_bot:
                        msg = (
                            f"All {len(bot_blocked)} page(s) were blocked by "
                            "bot protection. Add a browser session for this "
                            "domain in Settings → Sessions, then retry."
                        )
                        code = "BOT_PROTECTION_BLOCKED"
                    else:
                        msg = (
                            f"All {processed_pages} pages failed or "
                            "were blocked during extraction"
                        )
                        code = "ALL_PAGES_FAILED"
                    await _mark_project_failed(db, project, msg, code)
                return

            if spec.mode == ExtractionMode.STRUCTURED and total_records == 0:
                logger.warning(
                    "extraction.no_structured_records",
                    extra={
                        "project_id": project_id,
                        "pages_attempted": processed_pages,
                        "pages_extracted": pages_extracted,
                        "pages_zero_match": pages_zero_match,
                    },
                )
                project = await db.get(Project, project_id)
                if project and not project.is_terminal:
                    await _mark_project_failed(
                        db,
                        project,
                        (
                            f"No records were extracted from {pages_fetched_ok} "
                            f"successfully fetched page(s) — the selectors matched "
                            f"nothing. Adjust the fields and run Preview again."
                        ),
                        "NO_RECORDS_EXTRACTED",
                    )
                return

            project = await db.get(Project, project_id)
            if not project or project.is_terminal:
                return
            if project.state == ProjectState.EXTRACTING:
                project.transition_to(ProjectState.EXPORTING)
            elif project.can_transition_to(ProjectState.EXPORTING):
                project.transition_to(ProjectState.EXPORTING)
            else:
                logger.warning(
                    "project_extraction.finalization_skipped",
                    extra={
                        "project_id": project_id,
                        "state": project.state.value,
                    },
                )
                return

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
            await record_project_event(
                project_id,
                project.user_id,
                "extraction.completed",
                message=(
                    f"Extraction completed — {total_records} record(s) "
                    f"from {processed_pages} page(s)."
                ),
                metadata={
                    "records": total_records,
                    "pages": processed_pages,
                    "pages_extracted": pages_extracted,
                },
            )

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
