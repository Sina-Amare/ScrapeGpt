"""Real-DB verification of the run-scoped, non-destructive extraction model.

Runs in a single event loop (asyncio.run), so it avoids the per-test-loop
asyncpg pool issues that make these checks flaky under pytest. Exercises the
actual Postgres guarantees the unit/fake tests can't:

  * non-destructive start (prior records/exports + current run untouched)
  * EXTRACTION_ALREADY_RUNNING pre-check
  * active-run partial unique index
  * record idempotency (ON CONFLICT DO NOTHING)
  * lease fencing (_claim_pending_page / _still_owns_lease)
  * run-scoped reads (list_records / count_records)

Run manually (needs the DB at head): python -m tests.manual.verify_extraction_runs
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.core.logging_config import configure_logging
from app.db.database import async_session_factory
from app.models.job import (
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
from app.models.user import User
from app.services.project_extraction import (
    ExtractionAlreadyRunningError,
    _claim_pending_page,
    _still_owns_lease,
    count_records,
    list_records,
    start_project_extraction,
)

configure_logging()
logger = logging.getLogger(__name__)


async def _make_project(db, *, state=ProjectState.PREVIEW_READY):
    user = User(email=f"runverify-{uuid.uuid4().hex}@t.com", hashed_password="x")
    db.add(user)
    await db.flush()
    project = Project(
        user_id=user.id, url="https://example.com/",
        normalized_url="https://example.com/", state=state,
        extraction_mode=ExtractionMode.STRUCTURED,
    )
    db.add(project)
    await db.flush()
    spec = ExtractionSpec(
        project_id=project.id, mode=ExtractionMode.STRUCTURED,
        fields=[{"name": "Title", "selector": ".t", "selected": True}],
        content_config={}, url_patterns=[], page_limit=10, export_format="csv",
        crawl_scope={"mode": "CURRENT_PAGE", "status": "USER_CONFIRMED"},
    )
    db.add(spec)
    await db.flush()
    return user, project, spec


async def main() -> int:
    failures = 0
    created_users: list[int] = []

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        if cond:
            logger.info("OK %s", label)
        else:
            logger.error("FAIL %s", label)
            failures += 1

    async with async_session_factory() as db:
        # 1. Non-destructive start + 2. already-running guard.
        user, project, spec = await _make_project(db)
        created_users.append(user.id)
        old_run = ExtractionRun(project_id=project.id, spec_id=spec.id,
                                state=ExtractionRunState.COMPLETED.value)
        db.add(old_run)
        await db.flush()
        db.add(ExtractedRecord(project_id=project.id, extraction_run_id=old_run.id,
                               source_url="u", raw_data={"v": "old"}, normalized_data={"v": "old"}))
        db.add(Export(project_id=project.id, extraction_run_id=old_run.id, format="csv", record_count=1))
        project.current_extraction_run_id = old_run.id
        project.state = ProjectState.COMPLETED
        await db.commit()

        new_run = await start_project_extraction(db, project, spec)
        await db.commit()
        check(new_run.id != old_run.id, "start created a new run")
        check(project.current_extraction_run_id == old_run.id,
              "current run NOT promoted until completion (non-destructive)")
        kept = await db.scalar(select(func.count(ExtractedRecord.id)).where(
            ExtractedRecord.extraction_run_id == old_run.id))
        check(kept == 1, "prior records preserved during new run")

        try:
            await start_project_extraction(db, project, spec)
            check(False, "second start should raise ALREADY_RUNNING")
        except ExtractionAlreadyRunningError:
            check(True, "second start raises EXTRACTION_ALREADY_RUNNING")
        await db.rollback()

    async with async_session_factory() as db:
        # 3. active-run partial unique index.
        user, project, spec = await _make_project(db)
        created_users.append(user.id)
        db.add(ExtractionRun(project_id=project.id, state=ExtractionRunState.RUNNING.value))
        await db.flush()
        db.add(ExtractionRun(project_id=project.id, state=ExtractionRunState.RUNNING.value))
        try:
            await db.flush()
            check(False, "two active runs should violate the partial unique index")
        except IntegrityError:
            check(True, "partial unique index blocks a second active run")
        await db.rollback()

    async with async_session_factory() as db:
        # 4. record idempotency.
        user, project, spec = await _make_project(db)
        created_users.append(user.id)
        run = ExtractionRun(project_id=project.id, state=ExtractionRunState.RUNNING.value)
        db.add(run)
        await db.flush()
        page = CrawlPage(project_id=project.id, extraction_run_id=run.id, url="u",
                         normalized_url="u", state=CrawlPageState.EXTRACTED, depth=0)
        db.add(page)
        await db.flush()
        rows = [{
            "project_id": project.id, "extraction_run_id": run.id, "page_id": page.id,
            "record_ordinal": i, "source_url": "u", "raw_data": {"v": i},
            "normalized_data": {"v": i}, "warnings": [],
        } for i in range(2)]
        stmt = insert(ExtractedRecord).on_conflict_do_nothing(
            constraint="uq_extracted_records_run_page_ordinal")
        await db.execute(stmt.values(rows))
        await db.execute(stmt.values(rows))  # duplicate insert ignored
        await db.commit()
        n = await db.scalar(select(func.count(ExtractedRecord.id)).where(
            ExtractedRecord.extraction_run_id == run.id))
        check(n == 2, "idempotent insert: re-inserting same (run,page,ordinal) is ignored")

    async with async_session_factory() as db:
        # 5. lease fencing.
        user, project, spec = await _make_project(db)
        created_users.append(user.id)
        run = ExtractionRun(project_id=project.id, state=ExtractionRunState.RUNNING.value)
        db.add(run)
        await db.flush()
        db.add(CrawlPage(project_id=project.id, extraction_run_id=run.id, url="u",
                         normalized_url="u", state=CrawlPageState.PENDING, depth=0))
        await db.commit()
        claimed = await _claim_pending_page(db, run.id)
        ok = claimed is not None
        if ok:
            page, token = claimed
            ok = (page.state == CrawlPageState.FETCHING and page.lease_token == token
                  and await _still_owns_lease(db, page.id, token)
                  and not await _still_owns_lease(db, page.id, "other")
                  and await _claim_pending_page(db, run.id) is None)
        check(ok, "claim fences page (token) and lease check flips correctly")
        await db.rollback()

    async with async_session_factory() as db:
        # 6. run-scoped reads.
        user, project, spec = await _make_project(db)
        created_users.append(user.id)
        old_r = ExtractionRun(project_id=project.id, state=ExtractionRunState.COMPLETED.value)
        new_r = ExtractionRun(project_id=project.id, state=ExtractionRunState.COMPLETED.value)
        db.add_all([old_r, new_r])
        await db.flush()
        db.add(ExtractedRecord(project_id=project.id, extraction_run_id=old_r.id,
                               source_url="u", raw_data={"v": "old"}, normalized_data={"v": "old"}))
        db.add(ExtractedRecord(project_id=project.id, extraction_run_id=new_r.id,
                               source_url="u", raw_data={"v": "new"}, normalized_data={"v": "new"}))
        project.current_extraction_run_id = new_r.id
        await db.commit()
        cnt = await count_records(db, project.id)
        rows = await list_records(db, project.id, 0, 50)
        check(cnt == 1 and [r.normalized_data["v"] for r in rows] == ["new"],
              "reads are scoped to current_extraction_run_id")

    async with async_session_factory() as db:
        # 7. Orphaned-run cleanup: reclaims only aged, superseded/failed runs —
        # never the current (visible) run, never an active run, and never a run
        # within the retention window. Cascade removes the reclaimed run's
        # pages/records.
        from datetime import datetime, timezone, timedelta
        from app.services.watchdog import cleanup_orphaned_extraction_runs

        user, project, spec = await _make_project(db)
        created_users.append(user.id)
        old_ts = datetime.now(timezone.utc) - timedelta(days=100)
        now_ts = datetime.now(timezone.utc)

        current_run = ExtractionRun(project_id=project.id,
                                    state=ExtractionRunState.COMPLETED.value,
                                    finished_at=old_ts)
        failed_aged = ExtractionRun(project_id=project.id,
                                    state=ExtractionRunState.FAILED.value,
                                    finished_at=old_ts)
        failed_recent = ExtractionRun(project_id=project.id,
                                      state=ExtractionRunState.FAILED.value,
                                      finished_at=now_ts)
        running = ExtractionRun(project_id=project.id,
                                state=ExtractionRunState.RUNNING.value)
        db.add_all([current_run, failed_aged, failed_recent, running])
        await db.flush()
        # Capture ids before commit (expire_on_commit must not force a reload of
        # the aged run after another session deletes it).
        current_id, aged_id = current_run.id, failed_aged.id
        recent_id, running_id = failed_recent.id, running.id
        page = CrawlPage(project_id=project.id, extraction_run_id=aged_id,
                         url="u", normalized_url="u",
                         state=CrawlPageState.EXTRACTED, depth=0)
        db.add(page)
        await db.flush()
        db.add(ExtractedRecord(project_id=project.id, extraction_run_id=aged_id,
                               page_id=page.id, record_ordinal=0, source_url="u",
                               raw_data={"v": 1}, normalized_data={"v": 1}))
        project.current_extraction_run_id = current_id
        await db.commit()

        reclaimed = await cleanup_orphaned_extraction_runs()
        surviving = set((await db.execute(
            select(ExtractionRun.id).where(ExtractionRun.project_id == project.id)
        )).scalars().all())
        rec_left = await db.scalar(select(func.count(ExtractedRecord.id)).where(
            ExtractedRecord.extraction_run_id == aged_id))
        check(
            reclaimed >= 1
            and aged_id not in surviving
            and {current_id, recent_id, running_id} <= surviving
            and rec_left == 0,
            "orphaned-run cleanup deletes only aged superseded run; keeps "
            "current/recent/active; cascade removes its records",
        )

    # Cleanup (fresh session, FK cascade wipes the project tree).
    async with async_session_factory() as db:
        for uid in created_users:
            await db.execute(delete(User).where(User.id == uid))
        await db.commit()

    logger.info("verify_extraction_runs done failures=%s", failures)
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
