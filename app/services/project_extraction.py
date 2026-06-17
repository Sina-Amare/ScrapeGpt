"""Project extraction executor: crawl same-site pages and extract real records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.log_context import set_page_context, set_task_context
from app.core import metrics
from app.db.database import async_session_factory
from app.models.job import (
    ACTIVE_EXTRACTION_RUN_STATES,
    CrawlPage,
    CrawlPageState,
    Export,
    ExtractedRecord,
    ExtractionMode,
    ExtractionRun,
    ExtractionRunState,
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


class ExtractionAlreadyRunningError(RuntimeError):
    """Raised when a project already has an active (QUEUED/RUNNING) run."""

    code = "EXTRACTION_ALREADY_RUNNING"


async def _active_run(db: AsyncSession, project_id: int) -> ExtractionRun | None:
    return (
        await db.execute(
            select(ExtractionRun).where(
                ExtractionRun.project_id == project_id,
                ExtractionRun.state.in_(ACTIVE_EXTRACTION_RUN_STATES),
            )
        )
    ).scalars().first()


async def start_project_extraction(
    db: AsyncSession,
    project: Project,
    spec: ExtractionSpec,
    *,
    allow_unconfirmed: bool = False,
) -> ExtractionRun:
    """Create a new extraction run and queue its seed page (non-destructive).

    Serializes concurrent starts: the project row is locked ``FOR UPDATE`` and a
    pre-check (backed by a partial unique index) rejects a second active run with
    :class:`ExtractionAlreadyRunningError`. Prior pages/records/exports are NOT
    deleted — the new run is invisible to read endpoints until it completes and
    is promoted to ``project.current_extraction_run_id``.

    Enforces the scope confirmation policy (``ScopeConfirmationError``).
    """
    assert_scope_confirmed(
        spec.crawl_scope,
        allow_unconfirmed=allow_unconfirmed,
        allow_legacy_missing=True,
        project_id=project.id,
    )

    # Lock the project row so two concurrent /extract calls serialize here.
    await db.execute(
        select(Project.id).where(Project.id == project.id).with_for_update()
    )
    if await _active_run(db, project.id) is not None:
        raise ExtractionAlreadyRunningError(
            f"Project {project.id} already has an extraction in progress."
        )

    run = ExtractionRun(
        project_id=project.id,
        spec_id=spec.id,
        spec_hash=_spec_hash(spec),
        state=ExtractionRunState.RUNNING.value,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    try:
        await db.flush()  # assigns run.id; partial unique index is the final guard
    except IntegrityError as exc:
        await db.rollback()
        raise ExtractionAlreadyRunningError(
            f"Project {project.id} already has an extraction in progress."
        ) from exc

    project.transition_to(ProjectState.DISCOVERING)
    project.error = None
    project.error_code = None

    normalized = normalize_url(project.normalized_url or project.url)
    db.add(
        CrawlPage(
            project_id=project.id,
            extraction_run_id=run.id,
            url=project.url,
            normalized_url=normalized,
            state=CrawlPageState.PENDING,
            depth=0,
        )
    )
    await db.flush()
    return run


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


async def _claim_pending_page(
    db: AsyncSession, run_id: int
) -> tuple[CrawlPage, str] | None:
    """Atomically claim the next PENDING page for a run and fence it.

    Uses ``FOR UPDATE SKIP LOCKED`` so two workers on the same run never grab the
    same row, and stamps a fresh ``lease_token`` the worker must still own to
    finalize the page. Returns (page, lease_token) or None when none remain.
    """
    result = await db.execute(
        select(CrawlPage)
        .where(
            CrawlPage.extraction_run_id == run_id,
            CrawlPage.state == CrawlPageState.PENDING,
        )
        .order_by(CrawlPage.depth.asc(), CrawlPage.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    page = result.scalar_one_or_none()
    if page is None:
        return None
    token = uuid.uuid4().hex
    page.state = CrawlPageState.FETCHING
    page.lease_token = token
    page.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    await db.commit()
    return page, token


async def _still_owns_lease(db: AsyncSession, page_id: int, token: str) -> bool:
    """Re-check (under row lock) that this worker still owns the page lease."""
    result = await db.execute(
        select(CrawlPage).where(CrawlPage.id == page_id).with_for_update()
    )
    page = result.scalar_one_or_none()
    return page is not None and page.lease_token == token


async def _crawl_page_count(db: AsyncSession, run_id: int) -> int:
    value = await db.scalar(
        select(func.count(CrawlPage.id)).where(CrawlPage.extraction_run_id == run_id)
    )
    return int(value or 0)


async def _insert_discovered_pages(
    db: AsyncSession,
    *,
    project_id: int,
    run_id: int,
    urls: list[str],
    depth: int,
    remaining_slots: int,
) -> int:
    if remaining_slots <= 0 or not urls:
        return 0
    rows = [
        {
            "project_id": project_id,
            "extraction_run_id": run_id,
            "url": url,
            "normalized_url": url,
            "state": CrawlPageState.PENDING.value,
            "depth": depth,
        }
        for url in urls[:remaining_slots]
    ]
    statement = insert(CrawlPage).values(rows).on_conflict_do_nothing(
        constraint="uq_crawl_pages_run_url"
    )
    result = await db.execute(statement)
    return int(result.rowcount or 0)


async def _mark_run_failed(
    db: AsyncSession, run_id: int | None, message: str, code: str
) -> None:
    """Mark a run FAILED without touching the project's current (visible) run."""
    if run_id is None:
        return
    run = await db.get(ExtractionRun, run_id)
    if run is not None and run.state in ACTIVE_EXTRACTION_RUN_STATES:
        run.state = ExtractionRunState.FAILED.value
        run.error = message
        run.error_code = code
        run.finished_at = datetime.now(timezone.utc)
        metrics.record_run_state("failed")
        if run.started_at is not None:
            metrics.observe_run_duration(
                (run.finished_at - run.started_at).total_seconds()
            )


async def _mark_project_failed(
    db: AsyncSession, project: Project, message: str, code: str,
    *, run_id: int | None = None,
) -> None:
    """Transition project to FAILED, enforcing the state machine.

    If the project is already FAILED, just update the error fields.
    If the transition is not allowed by the state machine, log an
    error and force the transition as a defensive fallback — this
    should not happen with the current transition table but must
    not leave the project in an active state.

    Also marks the active run FAILED (``run_id``) but never clears
    ``current_extraction_run_id`` — the previous completed run stays visible.
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
    await _mark_run_failed(db, run_id, message, code)
    await db.commit()
    await record_project_event(
        project.id,
        project.user_id,
        "extraction.failed",
        level="error",
        message=message,
        metadata={"error_code": code},
    )


class _ExtractState:
    """Lock-guarded counters shared across concurrent page workers."""

    def __init__(self, page_limit: int) -> None:
        self.page_limit = page_limit
        self.processed = 0
        self.records = 0
        self.canceled = False
        self.lock = asyncio.Lock()


async def _process_one_page(
    db: AsyncSession, ctx: Any, page: CrawlPage, lease_token: str
) -> int:
    """Fetch, discover from, and extract one claimed page using ``db``.

    Returns the number of records extracted (0 for a blocked / decode-failed /
    lease-lost / zero-match / fetch-error page). Raises ``InteractionError`` to
    signal the whole run must abort. Each concurrent worker calls this with its
    OWN session and shares no mutable Python state — page leasing
    (FOR UPDATE SKIP LOCKED + fencing token) and idempotent record inserts are
    the only coordination, exactly as the single-worker path relied on.
    """
    try:
        validated_url = validate_url(page.normalized_url)

        effective_render_mode = ctx.project.render_mode.value
        if (
            effective_render_mode == "AUTO"
            and isinstance(ctx.project.fetch_metadata, dict)
            and ctx.project.fetch_metadata.get("render_mode_used") == "BROWSER"
        ):
            effective_render_mode = "BROWSER"
        fetched = await fetch_url(
            validated_url,
            effective_render_mode,
            browser_session_cookies=ctx.session_cookies,
        )
        challenge_reason = await asyncio.to_thread(
            anti_bot_challenge_reason, fetched.html, fetched.final_url
        )
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
                    "project_id": ctx.project_id,
                    "page_id": page.id,
                    "url": fetched.final_url,
                    "reason": challenge_reason,
                },
            )
            return 0

        # Undecodable/garbled body — fail this page with a precise cause
        # instead of "extracting" 0 records from garbage.
        quality = await asyncio.to_thread(assess_html_quality, fetched.html)
        if quality.is_binary:
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
                    "project_id": ctx.project_id,
                    "page_id": page.id,
                    "url": fetched.final_url,
                },
            )
            return 0

        page.url = fetched.final_url
        page.normalized_url = normalize_url(fetched.final_url)

        current_count = await _crawl_page_count(db, ctx.run_id)
        remaining = max(0, ctx.page_limit - current_count)
        # Link classification parses HTML too — offload it.
        if isinstance(ctx.scope, dict) and ctx.scope_mode:
            links = await asyncio.to_thread(
                select_links_to_enqueue,
                html=fetched.html,
                page_url=fetched.final_url,
                root_url=ctx.validated_seed,
                scope=ctx.scope,
                legacy_patterns=ctx.spec.url_patterns or [],
                analysis=ctx.project.analysis if isinstance(ctx.project.analysis, dict) else None,
                remaining_slots=remaining,
                source_depth=page.depth,
            )
        else:
            # Legacy: no scope, fall back to same-site BFS.
            links = await asyncio.to_thread(
                discover_same_site_links,
                fetched.html,
                page_url=fetched.final_url,
                root_url=ctx.validated_seed,
                patterns=ctx.spec.url_patterns or [],
                limit=remaining,
            )

        await _insert_discovered_pages(
            db,
            project_id=ctx.project_id,
            run_id=ctx.run_id,
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
                    _url, recipes, cookies=ctx.session_cookies
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
                    v, _render, browser_session_cookies=ctx.session_cookies
                )
                out[vid] = vf.html
            return out

        extracted, variant_warnings = await extract_records_with_variants(
            base_html=fetched.html,
            source_url=fetched.final_url,
            project=ctx.project,
            spec=ctx.spec,
            max_records=settings.MAX_RECORDS_PER_PAGE,
            fetch_variant_htmls=_fetch_variant_htmls,
            fetch_variant_url_htmls=_fetch_variant_url_htmls,
        )
        for w in variant_warnings:
            logger.info(
                "extraction.variant_warning",
                extra={"project_id": ctx.project_id, "page_id": page.id, "warning": w},
            )

        # Fencing: only write/finalize if we still own the lease. If the
        # watchdog (or another worker) reclaimed this slow page, skip silently
        # so we don't double-insert under another owner.
        if not await _still_owns_lease(db, page.id, lease_token):
            logger.warning(
                "extraction.page_lease_lost",
                extra={"project_id": ctx.project_id, "page_id": page.id, "run_id": ctx.run_id},
            )
            await db.rollback()
            return 0

        if extracted:
            record_rows = [
                {
                    "project_id": ctx.project.id,
                    "extraction_run_id": ctx.run_id,
                    "page_id": page.id,
                    "record_ordinal": idx,
                    "source_url": fetched.final_url,
                    "raw_data": item.raw_data,
                    "normalized_data": item.normalized_data,
                    "warnings": item.warnings,
                }
                for idx, item in enumerate(extracted)
            ]
            # ON CONFLICT DO NOTHING on (run, page, ordinal) makes
            # re-processing the same page idempotent.
            await db.execute(
                insert(ExtractedRecord)
                .values(record_rows)
                .on_conflict_do_nothing(
                    constraint="uq_extracted_records_run_page_ordinal"
                )
            )
        logger.debug(
            "extraction.records_extracted",
            extra={
                "project_id": ctx.project_id,
                "page_id": page.id,
                "record_count": len(extracted),
                "warnings_count": sum(len(item.warnings or []) for item in extracted),
            },
        )
        page.lease_expires_at = None
        if extracted:
            page.state = CrawlPageState.EXTRACTED
            page.error = None
            page.block_reason = None
            metrics.record_page_outcome("extracted")
        else:
            # Fetched fine, but selectors matched nothing. Mark it non-success
            # (FAILED + reason) so progress and the UI don't show a misleading
            # "extracted" page. The project still ends as NO_RECORDS_EXTRACTED
            # (not ALL_PAGES_FAILED) because the post-loop counts these as
            # fetched-OK pages.
            page.state = CrawlPageState.FAILED
            page.block_reason = "SELECTOR_ZERO_MATCH"
            page.error = "Selectors matched no elements on this page."
            metrics.record_page_outcome("zero_match")
        await db.commit()
        return len(extracted)

    except InteractionError as exc:
        # Spec-level variant config problem (browser missing / too many
        # combinations). Every page would fail identically, so abort the whole
        # run with the precise code. Re-raised so the worker pool stops.
        page.state = CrawlPageState.FAILED
        page.error = str(exc)
        page.block_reason = exc.code
        page.lease_expires_at = None
        await db.commit()
        await _mark_project_failed(db, ctx.project, str(exc), exc.code, run_id=ctx.run_id)
        logger.warning(
            "extraction.interaction_failed",
            extra={"project_id": ctx.project_id, "error_code": exc.code},
        )
        raise

    except (FetchError, URLValidationError) as exc:
        page.state = CrawlPageState.FAILED
        page.retry_count += 1
        page.error = str(exc)
        page.lease_expires_at = None
        await db.commit()
        logger.error(
            "extraction.page_failed",
            extra={
                "project_id": ctx.project_id,
                "page_id": page.id,
                "url": page.normalized_url,
                "error_type": type(exc).__name__,
            },
        )
        return 0


async def execute_project_extraction(
    project_id: int, spec_id: int, run_id: int | None = None
) -> None:
    """Run the crawl/extract pipeline for one run as a background task."""
    logger.info(
        "project_extraction.started",
        extra={"project_id": project_id, "spec_id": spec_id, "run_id": run_id},
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

        # Resolve the run this task owns. Legacy callers without run_id fall back
        # to the project's active run (defensive; the API always passes run_id).
        run = await db.get(ExtractionRun, run_id) if run_id is not None else None
        if run is None:
            run = await _active_run(db, project_id)
        if run is None or run.project_id != project_id:
            logger.error(
                "project_extraction.no_active_run",
                extra={"project_id": project_id, "run_id": run_id},
            )
            return
        if run.state not in ACTIVE_EXTRACTION_RUN_STATES:
            logger.info(
                "project_extraction.run_not_active",
                extra={"project_id": project_id, "run_id": run.id, "state": run.state},
            )
            return
        run_id = run.id

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

            ctx = SimpleNamespace(
                project=project,
                project_id=project_id,
                spec=spec,
                run_id=run_id,
                scope=scope,
                scope_mode=scope_mode,
                validated_seed=validated_seed,
                session_cookies=session_cookies,
                page_limit=page_limit,
            )
            shared = _ExtractState(page_limit)

            async def _worker() -> None:
                async with async_session_factory() as wdb:
                    while True:
                        async with shared.lock:
                            if shared.canceled or shared.processed >= page_limit:
                                return
                        if await _project_was_canceled(wdb, project_id):
                            async with shared.lock:
                                shared.canceled = True
                            logger.info(
                                "project_extraction.canceled",
                                extra={"project_id": project_id},
                            )
                            return
                        claimed = await _claim_pending_page(wdb, run_id)
                        if claimed is None:
                            return
                        page, lease_token = claimed
                        set_page_context(page_id=page.id)
                        try:
                            n = await _process_one_page(wdb, ctx, page, lease_token)
                        except InteractionError:
                            # Run already marked FAILED by _process_one_page;
                            # stop every worker.
                            async with shared.lock:
                                shared.canceled = True
                                shared.processed += 1
                            return
                        async with shared.lock:
                            shared.processed += 1
                            shared.records += n
                        if settings.MIN_CRAWL_DELAY_MS:
                            await asyncio.sleep(settings.MIN_CRAWL_DELAY_MS / 1000)

            # Bounded concurrency: each worker drains the shared page queue with
            # its OWN session. Leasing (FOR UPDATE SKIP LOCKED + fencing token)
            # and idempotent record inserts make overlap safe. Defaults to 1
            # worker (sequential) when CRAWL_CONCURRENCY is unset/absent.
            concurrency = max(
                1, min(getattr(settings, "CRAWL_CONCURRENCY", 1), max(1, page_limit))
            )
            await asyncio.gather(*(_worker() for _ in range(concurrency)))
            # Workers committed on their own sessions; refresh so the
            # finalization below (this session) reads the latest project state.
            await db.refresh(project)
            processed_pages = shared.processed
            total_records = shared.records

            # Post-loop completion check: if no pages were successfully
            # extracted, the project should fail rather than complete
            # with zero results. This covers the case where every page
            # hit a fetch error or URL validation error.
            # Per-page failure reasons are already stored on each
            # CrawlPage row for debugging.
            pages_extracted = await db.scalar(
                select(func.count(CrawlPage.id)).where(
                    CrawlPage.extraction_run_id == run_id,
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
                    CrawlPage.extraction_run_id == run_id,
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
                            CrawlPage.extraction_run_id == run_id,
                            CrawlPage.state == CrawlPageState.BLOCKED,
                        )
                    )).scalars().all()
                    failed_count = int(await db.scalar(
                        select(func.count(CrawlPage.id)).where(
                            CrawlPage.extraction_run_id == run_id,
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
                    await _mark_project_failed(db, project, msg, code, run_id=run_id)
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
                        run_id=run_id,
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
                            ExtractedRecord.extraction_run_id == run_id
                        )
                    )
                ).scalars().all()
                pages_total = processed_pages
                pages_failed = sum(
                    1
                    for page_row in (
                        await db.execute(
                            select(CrawlPage).where(CrawlPage.extraction_run_id == run_id)
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

            # Use the authoritative DB count for this run (idempotent inserts
            # mean total_records could double-count on a re-leased page).
            run_record_count = len(records)
            db.add(
                Export(
                    project_id=project.id,
                    extraction_run_id=run_id,
                    format=spec.export_format or "csv",
                    record_count=run_record_count,
                    spec_hash=_spec_hash(spec),
                )
            )
            # Promote this run: mark it COMPLETED and make it the project's
            # visible run. Only now do prior results get superseded.
            run = await db.get(ExtractionRun, run_id)
            if run is not None:
                run.state = ExtractionRunState.COMPLETED.value
                run.finished_at = datetime.now(timezone.utc)
                run.total_pages = await _crawl_page_count(db, run_id)
                run.total_records = run_record_count
                metrics.record_run_state("completed")
                if run.started_at is not None:
                    metrics.observe_run_duration(
                        (run.finished_at - run.started_at).total_seconds()
                    )
            project.current_extraction_run_id = run_id
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
                await _mark_project_failed(db, project, str(exc), exc.code, run_id=run_id)
        except Exception as exc:
            logger.exception("project_extraction.failed", extra={"project_id": project_id, "error": str(exc)})
            project = await db.get(Project, project_id)
            if project and project.state != ProjectState.CANCELED:
                await _mark_project_failed(db, project, f"Extraction failed: {exc}", "EXTRACTION_FAILED", run_id=run_id)


async def _current_run_id(db: AsyncSession, project_id: int) -> int | None:
    """The completed run the read endpoints should surface for a project."""
    return await db.scalar(
        select(Project.current_extraction_run_id).where(Project.id == project_id)
    )


async def list_records(
    db: AsyncSession,
    project_id: int,
    skip: int,
    limit: int,
) -> list[ExtractedRecord]:
    run_id = await _current_run_id(db, project_id)
    if run_id is None:
        return []
    result = await db.execute(
        select(ExtractedRecord)
        .where(ExtractedRecord.extraction_run_id == run_id)
        .order_by(ExtractedRecord.id.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_records(db: AsyncSession, project_id: int) -> int:
    run_id = await _current_run_id(db, project_id)
    if run_id is None:
        return 0
    result = await db.scalar(
        select(func.count(ExtractedRecord.id)).where(
            ExtractedRecord.extraction_run_id == run_id
        )
    )
    return int(result or 0)
