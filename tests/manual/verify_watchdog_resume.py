"""Real-DB verification of watchdog crash-recovery resume (A1).

The mocked unit tests in tests/services/test_reliability_hardening.py cover the
resume *decision* logic, but they cannot exercise the actual SQL: the
Project<->ExtractionRun join, the correlated per-run page-activity subquery used
as the liveness signal, the resume_count bump, and the hard-fail UPDATEs. This
script does, against Postgres at head.

Scenarios:
  A. Stalled EXTRACTING run under the resume cap -> re-dispatched (resume_count
     bumped, project stays EXTRACTING, _schedule_resume called).
  B. Stalled run at the resume cap -> hard-failed (project FAILED + run FAILED,
     error_code EXTRACTION_RESUME_EXHAUSTED, NOT re-dispatched).
  C. Run with FRESH page activity -> left alone (the liveness signal is per-run
     page activity, NOT Project.updated_at, so a healthy long crawl is never
     re-dispatched into a duplicate worker).

Run manually (needs the DB at head, no network required):
    python -m tests.manual.verify_watchdog_resume
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.db.database import async_session_factory
from app.models.job import (
    CrawlPage,
    CrawlPageState,
    ExtractionMode,
    ExtractionRun,
    ExtractionRunState,
    ExtractionSpec,
    Project,
    ProjectState,
)
from app.models.user import User
from app.services import watchdog

configure_logging()
logger = logging.getLogger(__name__)


async def _make_run(db, *, resume_count: int, page_age_minutes: int):
    """Create an EXTRACTING project with one active run and one page whose
    created_at is ``page_age_minutes`` in the past (its liveness signal)."""
    now = datetime.now(timezone.utc)
    user = User(email=f"wdog-{uuid.uuid4().hex}@t.com", hashed_password="x")
    db.add(user)
    await db.flush()
    project = Project(
        user_id=user.id, url="https://example.com/",
        normalized_url="https://example.com/", state=ProjectState.EXTRACTING,
        extraction_mode=ExtractionMode.STRUCTURED,
    )
    db.add(project)
    await db.flush()
    spec = ExtractionSpec(
        project_id=project.id, mode=ExtractionMode.STRUCTURED,
        fields=[{"name": "Title", "selector": ".t", "selected": True}],
        content_config={}, url_patterns=[], page_limit=10, export_format="csv",
        crawl_scope={"mode": "CURRENT_PAGE", "status": "USER_CONFIRMED"},
        interaction_profile={},
    )
    db.add(spec)
    await db.flush()
    run = ExtractionRun(
        project_id=project.id, spec_id=spec.id,
        state=ExtractionRunState.RUNNING.value,
        started_at=now - timedelta(minutes=page_age_minutes),
        resume_count=resume_count,
    )
    db.add(run)
    await db.flush()
    db.add(CrawlPage(
        project_id=project.id, extraction_run_id=run.id,
        url="https://example.com/", normalized_url="https://example.com/",
        state=CrawlPageState.PENDING, depth=0,
        created_at=now - timedelta(minutes=page_age_minutes),
    ))
    await db.commit()
    return project.id, spec.id, run.id, user.id


async def _cleanup(db, user_id: int) -> None:
    pid = (await db.execute(
        Project.__table__.select().where(Project.user_id == user_id)
    )).first()
    if pid is not None:
        await db.execute(delete(Project).where(Project.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def _check(name: str, ok: bool) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return ok


async def main() -> None:
    stale = settings.WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES + 10
    fresh = 1
    cap = settings.WATCHDOG_MAX_RESUME_ATTEMPTS
    all_ok = True
    scheduled: list = []

    original_schedule = watchdog._schedule_resume
    watchdog._schedule_resume = (
        lambda pid, sid, rid: scheduled.append((pid, sid, rid))
    )
    watchdog._resuming_run_ids.clear()
    created_users: list[int] = []
    try:
        # --- A. Stalled, under cap -> resumed ---
        async with async_session_factory() as db:
            pid, sid, rid, uid = await _make_run(
                db, resume_count=0, page_age_minutes=stale
            )
            created_users.append(uid)
        scheduled.clear()
        failed = await watchdog.cleanup_stuck_projects()
        async with async_session_factory() as db:
            project = await db.get(Project, pid)
            run = await db.get(ExtractionRun, rid)
            all_ok &= _check("A: project stays EXTRACTING",
                             project.state == ProjectState.EXTRACTING)
            all_ok &= _check("A: resume_count bumped to 1",
                             run.resume_count == 1)
            all_ok &= _check("A: run re-dispatched", (pid, sid, rid) in scheduled)
            all_ok &= _check("A: not counted as hard-failed", failed == 0)

        # --- B. At cap -> hard-failed ---
        async with async_session_factory() as db:
            pid, sid, rid, uid = await _make_run(
                db, resume_count=cap, page_age_minutes=stale
            )
            created_users.append(uid)
        scheduled.clear()
        watchdog._resuming_run_ids.clear()
        await watchdog.cleanup_stuck_projects()
        async with async_session_factory() as db:
            project = await db.get(Project, pid)
            run = await db.get(ExtractionRun, rid)
            all_ok &= _check("B: project FAILED",
                             project.state == ProjectState.FAILED)
            all_ok &= _check("B: error_code EXTRACTION_RESUME_EXHAUSTED",
                             project.error_code == "EXTRACTION_RESUME_EXHAUSTED")
            all_ok &= _check("B: run FAILED",
                             run.state == ExtractionRunState.FAILED.value)
            all_ok &= _check("B: not re-dispatched", (pid, sid, rid) not in scheduled)

        # --- C. Fresh page activity -> left alone ---
        async with async_session_factory() as db:
            pid, sid, rid, uid = await _make_run(
                db, resume_count=0, page_age_minutes=fresh
            )
            created_users.append(uid)
        scheduled.clear()
        watchdog._resuming_run_ids.clear()
        await watchdog.cleanup_stuck_projects()
        async with async_session_factory() as db:
            project = await db.get(Project, pid)
            run = await db.get(ExtractionRun, rid)
            all_ok &= _check("C: fresh run untouched (still EXTRACTING)",
                             project.state == ProjectState.EXTRACTING)
            all_ok &= _check("C: fresh run not re-dispatched",
                             (pid, sid, rid) not in scheduled)
            all_ok &= _check("C: resume_count unchanged", run.resume_count == 0)
    finally:
        watchdog._schedule_resume = original_schedule
        watchdog._resuming_run_ids.clear()
        async with async_session_factory() as db:
            for uid in created_users:
                await _cleanup(db, uid)

    print("\nRESULT:", "ALL PASS" if all_ok else "FAILURES ABOVE")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
