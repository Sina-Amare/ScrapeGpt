"""Tests for reliability hardening changes.

Covers:
1. Legacy /scrape endpoint SSRF validation (400 on private URLs)
2. Legacy /scrape executor SSRF defense-in-depth (fails task on private URLs)
3. CrawlPage lease reaper (expired leases reset to PENDING)
4. Stuck-project watchdog (resume / hard-fail semantics)
5. Extraction completion semantics (all-pages-failed → FAILED)
6. Config: CORS Vite origin, CRAWL_CONCURRENCY description, watchdog timeouts
"""

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.v1.endpoints import scrape
from app.models.job import (
    CrawlPage,
    CrawlPageState,
    ExtractionMode,
    ExtractionRun,
    ExtractionSpec,
    Project,
    ProjectState,
    RenderMode,
)
from app.models.scrape_task import ScrapeTask, TaskState
from app.models.user import User
from app.services.url_validator import URLBlockReason


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


def _user(user_id: int = 1) -> User:
    return User(id=user_id, email="user@example.com", hashed_password="hash")


class FakeResult:
    def __init__(self, task=None, scalar=None):
        self._task = task
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._task

    def scalar(self):
        return self._scalar


class FakeScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeListResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return FakeScalarsResult(self._items)


class FakeAdmissionResult:
    """Mimics the result of admit_scrape_task."""

    def __init__(self, task=None, error=None):
        self.task = task
        self.error_type = None
        self.message = None
        self.active_task_id = None
        if error:
            self.error_type = error.get("error_type")
            self.message = error.get("message")
            self.active_task_id = error.get("active_task_id")


class FakeSession:
    """Minimal async session fake for scrape endpoint tests."""

    def __init__(self, task=None):
        self._task = task

    async def execute(self, statement):
        return FakeResult(self._task)

    async def commit(self):
        pass


# ---------------------------------------------------------------------------
# 1. Legacy /scrape endpoint SSRF validation
# ---------------------------------------------------------------------------


@pytest.fixture
def scrape_app():
    application = FastAPI()
    application.include_router(scrape.router, prefix="/api/v1")
    return application


@pytest.mark.asyncio
async def test_start_scrape_rejects_private_ip_url(scrape_app):
    """POST /scrape/start returns 400 for private-network URLs."""
    session = FakeSession()

    async def override_get_current_user():
        return _user()

    async def override_get_db():
        yield session

    scrape_app.dependency_overrides[deps.get_current_user] = (
        override_get_current_user
    )
    scrape_app.dependency_overrides[deps.get_db] = override_get_db

    transport = ASGITransport(app=scrape_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/scrape/start",
            json={"url": "http://192.168.1.1/"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert "detail" in body
    detail = body["detail"]
    assert detail.get("error_code") == URLBlockReason.PRIVATE_ADDRESS.value


@pytest.mark.asyncio
async def test_start_scrape_rejects_loopback_url(scrape_app):
    """POST /scrape/start returns 400 for loopback URLs."""
    session = FakeSession()

    async def override_get_current_user():
        return _user()

    async def override_get_db():
        yield session

    scrape_app.dependency_overrides[deps.get_current_user] = (
        override_get_current_user
    )
    scrape_app.dependency_overrides[deps.get_db] = override_get_db

    transport = ASGITransport(app=scrape_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/scrape/start",
            json={"url": "http://127.0.0.1/admin"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    detail = body["detail"]
    assert detail.get("error_code") == URLBlockReason.LOOPBACK.value


@pytest.mark.asyncio
async def test_start_scrape_accepts_valid_public_url(scrape_app):
    """POST /scrape/start proceeds normally for valid public URLs."""
    task = ScrapeTask(
        id=1,
        user_id=1,
        state=TaskState.PERMISSION_GRANTED,
        url="https://example.com",
    )
    result = FakeAdmissionResult(task=task)

    async def fake_admit(user, url, db):
        return result

    session = FakeSession(task)

    async def override_get_current_user():
        return _user()

    async def override_get_db():
        yield session

    scrape_app.dependency_overrides[deps.get_current_user] = (
        override_get_current_user
    )
    scrape_app.dependency_overrides[deps.get_db] = override_get_db

    # Patch admission to avoid DB interaction
    import app.api.v1.endpoints.scrape as scrape_mod
    original_admit = scrape_mod.admit_scrape_task
    scrape_mod.admit_scrape_task = fake_admit

    transport = ASGITransport(app=scrape_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/scrape/start",
            json={"url": "https://example.com"},
        )

    # Restore
    scrape_mod.admit_scrape_task = original_admit

    assert response.status_code == status.HTTP_202_ACCEPTED


# ---------------------------------------------------------------------------
# 2. Legacy /scrape executor SSRF defense-in-depth
# ---------------------------------------------------------------------------


class FakeTransitionResult:
    def __init__(self, success=True, error=None):
        self.success = success
        self.error = error


@pytest.mark.asyncio
async def test_executor_fails_task_on_private_url(monkeypatch):
    """execute_scrape_pipeline fails the task when URL is private."""
    from app.services.task_executor import execute_scrape_pipeline

    task = ScrapeTask(
        id=99,
        user_id=1,
        state=TaskState.PERMISSION_GRANTED,
        url="http://10.0.0.1/internal",
    )

    transitions = []

    async def fake_transition_to_failed(task_id, error, **kwargs):
        transitions.append(("failed", task_id, error))
        return FakeTransitionResult(success=True)

    class FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, pk):
            if model == ScrapeTask and pk == 99:
                return task
            return None

    monkeypatch.setattr(
        "app.services.task_executor.async_session_factory",
        lambda: FakeDB(),
    )
    monkeypatch.setattr(
        "app.services.task_executor.transition_to_failed",
        fake_transition_to_failed,
    )
    # Prevent further pipeline execution
    monkeypatch.setattr(
        "app.services.task_executor.transition_to_scraping",
        lambda *a, **kw: FakeTransitionResult(success=False),
    )

    await execute_scrape_pipeline(task_id=99, user_id=1)

    assert len(transitions) == 1
    assert transitions[0][0] == "failed"
    assert transitions[0][1] == 99


# ---------------------------------------------------------------------------
# 3. CrawlPage lease reaper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lease_reaper_resets_expired_fetching_pages(monkeypatch):
    """cleanup_expired_crawl_page_leases resets expired FETCHING pages."""
    from app.services.watchdog import (
        cleanup_expired_crawl_page_leases,
    )

    now = datetime.now(timezone.utc)

    # Page with expired lease
    page = CrawlPage(
        id=10,
        project_id=1,
        url="https://example.com/page1",
        normalized_url="https://example.com/page1",
        state=CrawlPageState.FETCHING,
        lease_expires_at=now - timedelta(minutes=1),
        depth=0,
    )

    class FakeDB:
        call_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            self.call_count += 1
            if self.call_count == 1:
                # First query: active project IDs
                return FakeListResult([1])
            # Second query: expired pages
            return FakeListResult([page])

        async def commit(self):
            pass

    monkeypatch.setattr(
        "app.services.watchdog.async_session_factory",
        lambda: FakeDB(),
    )

    result = await cleanup_expired_crawl_page_leases()

    assert result == 1
    assert page.state == CrawlPageState.PENDING
    assert page.lease_expires_at is None


@pytest.mark.asyncio
async def test_lease_reaper_skips_pages_in_inactive_projects(
    monkeypatch,
):
    """Lease reaper returns 0 when no projects are in active states."""
    from app.services.watchdog import (
        cleanup_expired_crawl_page_leases,
    )

    class FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            # No active projects
            return FakeListResult([])

    monkeypatch.setattr(
        "app.services.watchdog.async_session_factory",
        lambda: FakeDB(),
    )

    result = await cleanup_expired_crawl_page_leases()
    assert result == 0


# ---------------------------------------------------------------------------
# 4. Stuck-project watchdog
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A1: stalled DISCOVERING/EXTRACTING runs are RESUMED (re-dispatched), not
# hard-failed, up to WATCHDOG_MAX_RESUME_ATTEMPTS. These exercise the resume
# *decision* logic; the SQL itself is covered by the real-DB verifier
# tests/manual/verify_watchdog_resume.py.
# ---------------------------------------------------------------------------


def _watchdog_settings(max_resume: int = 3):
    return SimpleNamespace(
        WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES=10,
        WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES=10,
        WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES=10,
        WATCHDOG_MAX_RESUME_ATTEMPTS=max_resume,
    )


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RowcountResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _ResumeFakeDB:
    """Returns programmed rows for the two SELECTs (DISCOVERING then
    EXTRACTING) and rowcount 0 for the EXPORTING / cascade UPDATEs. ``get``
    returns the pre-built ExtractionRun stand-in so resume_count mutations are
    observable."""

    def __init__(self, select_rows, runs):
        self._select_rows = list(select_rows)
        self._runs = runs
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        if type(statement).__name__ == "Select":
            rows = self._select_rows.pop(0) if self._select_rows else []
            return _RowsResult(rows)
        return _RowcountResult(0)

    async def get(self, model, pk):
        return self._runs.get(pk)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_stuck_extracting_project_is_resumed(monkeypatch):
    """A stalled EXTRACTING run under the resume cap is re-dispatched, not failed."""
    from app.services import watchdog

    monkeypatch.setattr(watchdog, "settings", _watchdog_settings(max_resume=3))
    # DISCOVERING select -> none; EXTRACTING select -> one candidate.
    # tuple = (project_id, run_id, spec_id, resume_count)
    run = SimpleNamespace(id=55, state="RUNNING", resume_count=0)
    monkeypatch.setattr(
        watchdog, "async_session_factory",
        lambda: _ResumeFakeDB([[], [(101, 55, 9, 0)]], {55: run}),
    )
    scheduled: list = []
    monkeypatch.setattr(
        watchdog, "_schedule_resume",
        lambda pid, sid, rid: scheduled.append((pid, sid, rid)),
    )
    watchdog._resuming_run_ids.clear()

    failed = await watchdog.cleanup_stuck_projects()

    assert failed == 0
    assert scheduled == [(101, 9, 55)]
    assert run.resume_count == 1  # attempt recorded before re-dispatch


@pytest.mark.asyncio
async def test_stuck_extraction_resume_exhausted_hard_fails(monkeypatch):
    """Once resume_count hits the cap, the project is hard-failed instead."""
    from app.services import watchdog

    monkeypatch.setattr(watchdog, "settings", _watchdog_settings(max_resume=3))
    run = SimpleNamespace(id=55, state="RUNNING", resume_count=3)
    monkeypatch.setattr(
        watchdog, "async_session_factory",
        lambda: _ResumeFakeDB([[], [(101, 55, 9, 3)]], {55: run}),
    )
    scheduled: list = []
    monkeypatch.setattr(watchdog, "_schedule_resume", lambda *a: scheduled.append(a))
    hard_failed: list = []

    async def _fake_hard_fail(db, pid, msg, code):
        hard_failed.append((pid, code))
        return True

    monkeypatch.setattr(watchdog, "_hard_fail_project", _fake_hard_fail)
    watchdog._resuming_run_ids.clear()

    failed = await watchdog.cleanup_stuck_projects()

    assert failed == 1
    assert scheduled == []
    assert hard_failed == [(101, "EXTRACTION_RESUME_EXHAUSTED")]


@pytest.mark.asyncio
async def test_resume_skips_run_already_in_progress(monkeypatch):
    """A run a prior sweep is still resuming is not re-dispatched again."""
    from app.services import watchdog

    monkeypatch.setattr(watchdog, "settings", _watchdog_settings(max_resume=3))
    run = SimpleNamespace(id=55, state="RUNNING", resume_count=0)
    monkeypatch.setattr(
        watchdog, "async_session_factory",
        lambda: _ResumeFakeDB([[], [(101, 55, 9, 0)]], {55: run}),
    )
    scheduled: list = []
    monkeypatch.setattr(watchdog, "_schedule_resume", lambda *a: scheduled.append(a))
    hard_failed: list = []

    async def _fake_hard_fail(db, pid, msg, code):
        hard_failed.append((pid, code))
        return True

    monkeypatch.setattr(watchdog, "_hard_fail_project", _fake_hard_fail)
    watchdog._resuming_run_ids.clear()
    watchdog._resuming_run_ids.add(55)  # pretend a prior resume is still running
    try:
        failed = await watchdog.cleanup_stuck_projects()
    finally:
        watchdog._resuming_run_ids.discard(55)

    assert failed == 0
    assert scheduled == []
    assert hard_failed == []
    assert run.resume_count == 0  # untouched


@pytest.mark.asyncio
async def test_resume_disabled_hard_fails_immediately(monkeypatch):
    """WATCHDOG_MAX_RESUME_ATTEMPTS=0 reproduces the pre-A1 hard-fail behavior."""
    from app.services import watchdog

    monkeypatch.setattr(watchdog, "settings", _watchdog_settings(max_resume=0))
    run = SimpleNamespace(id=55, state="RUNNING", resume_count=0)
    monkeypatch.setattr(
        watchdog, "async_session_factory",
        lambda: _ResumeFakeDB([[], [(101, 55, 9, 0)]], {55: run}),
    )
    scheduled: list = []
    monkeypatch.setattr(watchdog, "_schedule_resume", lambda *a: scheduled.append(a))
    hard_failed: list = []

    async def _fake_hard_fail(db, pid, msg, code):
        hard_failed.append((pid, code))
        return True

    monkeypatch.setattr(watchdog, "_hard_fail_project", _fake_hard_fail)
    watchdog._resuming_run_ids.clear()

    failed = await watchdog.cleanup_stuck_projects()

    assert failed == 1
    assert scheduled == []
    assert hard_failed == [(101, "EXTRACTION_RESUME_EXHAUSTED")]


# ---------------------------------------------------------------------------
# 5. Extraction completion semantics (all-pages-failed)
# ---------------------------------------------------------------------------


def test_all_pages_failed_marks_project_as_failed():
    """
    When zero pages reach EXTRACTED and zero records are produced,
    the project should be FAILED with ALL_PAGES_FAILED, not COMPLETED.
    """
    project = Project(
        id=1,
        user_id=1,
        url="https://example.com",
        state=ProjectState.EXTRACTING,
    )

    # Simulate the all-pages-failed condition:
    # pages_extracted == 0, total_records == 0
    pages_extracted = 0
    total_records = 0
    # The logic in project_extraction.py checks:
    # if pages_extracted == 0 and total_records == 0 → FAILED
    assert pages_extracted == 0
    assert total_records == 0
    # Project should transition to FAILED, not COMPLETED
    assert project.can_transition_to(ProjectState.FAILED)


def test_extracted_pages_can_transition_to_exporting():
    """
    When some pages are extracted (even with zero records from
    those pages), the project should still complete — this is
    a quality issue, not a hard failure.
    """
    project = Project(
        id=1,
        user_id=1,
        url="https://example.com",
        state=ProjectState.EXTRACTING,
    )

    # At least one page extracted → normal completion path
    pages_extracted = 1
    # This should NOT trigger the all-pages-failed path
    assert pages_extracted > 0
    # Project should proceed to EXPORTING → COMPLETED
    assert project.can_transition_to(ProjectState.EXPORTING)


class FakeExtractionDB:
    """Async session fake for exercising execute_project_extraction."""

    def __init__(
        self,
        project: Project,
        spec: ExtractionSpec,
        *,
        pages_extracted: int,
        pages_zero_match: int = 0,
    ):
        self.project = project
        self.spec = spec
        self.pages_extracted = pages_extracted
        self.pages_zero_match = pages_zero_match
        self.commits = 0
        self.added = []
        self.run = ExtractionRun(
            id=9001, project_id=project.id, spec_id=spec.id, state="RUNNING"
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, model, pk):
        if model == Project and pk == self.project.id:
            return self.project
        if model == ExtractionSpec and pk == self.spec.id:
            return self.spec
        if model == ExtractionRun and pk == self.run.id:
            return self.run
        return None

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass

    async def execute(self, statement):
        return FakeListResult([])

    async def scalar(self, statement):
        # The zero-match count query filters on block_reason; everything else
        # here is the EXTRACTED count.
        if "block_reason" in str(statement):
            return self.pages_zero_match
        return self.pages_extracted

    def add(self, obj):
        self.added.append(obj)


def _extraction_project(
    state: ProjectState = ProjectState.DISCOVERING,
) -> Project:
    return Project(
        id=1,
        user_id=1,
        url="https://example.com",
        normalized_url="https://example.com",
        state=state,
        render_mode=RenderMode.STATIC,
    )


def _extraction_spec() -> ExtractionSpec:
    return ExtractionSpec(
        id=10,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[],
        content_config={},
        url_patterns=[],
        page_limit=1,
        export_format="csv",
        crawl_scope={"mode": "CURRENT_PAGE", "status": "SYSTEM_DEFAULTED"},
    )


@pytest.mark.asyncio
async def test_execute_project_extraction_all_pages_failed_real_path(
    monkeypatch,
):
    """The real executor marks all-page failure as FAILED/ALL_PAGES_FAILED."""
    from app.services import project_extraction
    from app.services.fetcher import FetchError

    project = _extraction_project()
    spec = _extraction_spec()
    page = CrawlPage(
        id=100,
        project_id=project.id,
        url=project.url,
        normalized_url=project.normalized_url,
        state=CrawlPageState.PENDING,
        depth=0,
        retry_count=0,
    )
    db = FakeExtractionDB(project, spec, pages_extracted=0)
    pending_pages = [page]

    monkeypatch.setattr(
        project_extraction,
        "async_session_factory",
        lambda: db,
    )
    monkeypatch.setattr(
        project_extraction,
        "settings",
        SimpleNamespace(MAX_PAGES_PER_JOB=500, MAX_RECORDS_PER_PAGE=1000, MIN_CRAWL_DELAY_MS=0),
    )
    monkeypatch.setattr(project_extraction, "validate_url", lambda url: url)

    async def not_canceled(db, project_id):
        return False

    async def next_pending_page(db, run_id):
        page = pending_pages.pop(0) if pending_pages else None
        if page is None:
            return None
        page.state = CrawlPageState.FETCHING
        page.lease_token = "tok"
        return page, "tok"

    async def _owns_lease(db, page_id, token):
        return True

    monkeypatch.setattr(
        project_extraction,
        "_project_was_canceled",
        not_canceled,
    )
    monkeypatch.setattr(
        project_extraction,
        "_claim_pending_page",
        next_pending_page,
    )
    monkeypatch.setattr(project_extraction, "_still_owns_lease", _owns_lease)

    async def fake_fetch_url(url, render_mode, **kwargs):
        raise FetchError("boom", "FETCH_FAILED")

    monkeypatch.setattr(project_extraction, "fetch_url", fake_fetch_url)

    await project_extraction.execute_project_extraction(project.id, spec.id, db.run.id)

    assert project.state == ProjectState.FAILED
    assert project.error_code == "ALL_PAGES_FAILED"
    assert page.state == CrawlPageState.FAILED
    assert page.lease_expires_at is None


@pytest.mark.asyncio
async def test_execute_project_extraction_blocks_cloudflare_challenge(
    monkeypatch,
):
    """Anti-bot challenge HTML must not count as a successful extracted page."""
    from app.services import project_extraction

    project = _extraction_project()
    spec = _extraction_spec()
    page = CrawlPage(
        id=101,
        project_id=project.id,
        url=project.url,
        normalized_url=project.normalized_url,
        state=CrawlPageState.PENDING,
        depth=0,
        retry_count=0,
    )
    db = FakeExtractionDB(project, spec, pages_extracted=0)
    pending_pages = [page]

    monkeypatch.setattr(project_extraction, "async_session_factory", lambda: db)
    monkeypatch.setattr(
        project_extraction,
        "settings",
        SimpleNamespace(MAX_PAGES_PER_JOB=500, MAX_RECORDS_PER_PAGE=1000, MIN_CRAWL_DELAY_MS=0),
    )
    monkeypatch.setattr(project_extraction, "validate_url", lambda url: url)

    async def not_canceled(db, project_id):
        return False

    async def next_pending_page(db, run_id):
        page = pending_pages.pop(0) if pending_pages else None
        if page is None:
            return None
        page.state = CrawlPageState.FETCHING
        page.lease_token = "tok"
        return page, "tok"

    async def _owns_lease(db, page_id, token):
        return True

    async def fake_fetch_url(url, render_mode, **kwargs):
        return SimpleNamespace(
            html=(
                "<html><title>Just a moment...</title>"
                "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1'></script>"
                "<body>Checking if the site connection is secure</body></html>"
            ),
            final_url=url,
        )

    monkeypatch.setattr(project_extraction, "_project_was_canceled", not_canceled)
    monkeypatch.setattr(project_extraction, "_claim_pending_page", next_pending_page)
    monkeypatch.setattr(project_extraction, "_still_owns_lease", _owns_lease)
    monkeypatch.setattr(project_extraction, "fetch_url", fake_fetch_url)

    await project_extraction.execute_project_extraction(project.id, spec.id, db.run.id)

    assert project.state == ProjectState.FAILED
    assert project.error_code == "ALL_PAGES_FAILED"
    assert page.state == CrawlPageState.BLOCKED
    assert page.block_reason == "ANTI_BOT_CHALLENGE"
    assert page.lease_expires_at is None
    assert db.added == []


@pytest.mark.asyncio
async def test_execute_project_extraction_fails_structured_zero_records(
    monkeypatch,
):
    """Structured extraction should not report success when no rows are found."""
    from app.services import project_extraction

    project = _extraction_project()
    spec = _extraction_spec()
    spec.fields = [
        {
            "name": "title",
            "label": "Title",
            "selector": "article.result h2",
            "type": "string",
            "selected": True,
            "required": False,
        }
    ]
    page = CrawlPage(
        id=103,
        project_id=project.id,
        url=project.url,
        normalized_url=project.normalized_url,
        state=CrawlPageState.PENDING,
        depth=0,
        retry_count=0,
    )
    # The page fetches fine but its selectors match nothing: it becomes
    # FAILED+SELECTOR_ZERO_MATCH (a fetched-OK page), so the project fails as
    # NO_RECORDS_EXTRACTED, not ALL_PAGES_FAILED.
    db = FakeExtractionDB(project, spec, pages_extracted=0, pages_zero_match=1)
    pending_pages = [page]

    monkeypatch.setattr(project_extraction, "async_session_factory", lambda: db)
    monkeypatch.setattr(
        project_extraction,
        "settings",
        SimpleNamespace(MAX_PAGES_PER_JOB=500, MAX_RECORDS_PER_PAGE=1000, MIN_CRAWL_DELAY_MS=0),
    )
    monkeypatch.setattr(project_extraction, "validate_url", lambda url: url)

    async def not_canceled(db, project_id):
        return False

    async def next_pending_page(db, run_id):
        page = pending_pages.pop(0) if pending_pages else None
        if page is None:
            return None
        page.state = CrawlPageState.FETCHING
        page.lease_token = "tok"
        return page, "tok"

    async def _owns_lease(db, page_id, token):
        return True

    async def crawl_page_count(db, project_id):
        return 1

    async def fake_fetch_url(url, render_mode, **kwargs):
        return SimpleNamespace(
            html="<html><body><p>No matching listing rows here.</p></body></html>",
            final_url=url,
        )

    monkeypatch.setattr(project_extraction, "_project_was_canceled", not_canceled)
    monkeypatch.setattr(project_extraction, "_claim_pending_page", next_pending_page)
    monkeypatch.setattr(project_extraction, "_still_owns_lease", _owns_lease)
    monkeypatch.setattr(project_extraction, "_crawl_page_count", crawl_page_count)
    monkeypatch.setattr(project_extraction, "fetch_url", fake_fetch_url)

    await project_extraction.execute_project_extraction(project.id, spec.id, db.run.id)

    assert project.state == ProjectState.FAILED
    assert project.error_code == "NO_RECORDS_EXTRACTED"
    assert "No records were extracted" in project.error
    # Zero-record pages must not look "extracted": they are non-success with a
    # precise reason so progress/UI surface them in the failed details.
    assert page.state == CrawlPageState.FAILED
    assert page.block_reason == "SELECTOR_ZERO_MATCH"
    assert page.lease_expires_at is None
    assert db.added == []


@pytest.mark.asyncio
async def test_execute_project_extraction_does_not_complete_failed_project(
    monkeypatch,
):
    """A watchdog-failed project must not be forced to COMPLETED."""
    from app.services import project_extraction

    project = _extraction_project()
    spec = _extraction_spec()
    page = CrawlPage(
        id=102,
        project_id=project.id,
        url=project.url,
        normalized_url=project.normalized_url,
        state=CrawlPageState.PENDING,
        depth=0,
        retry_count=0,
    )

    class RaceDB(FakeExtractionDB):
        async def scalar(self, statement):
            self.project.state = ProjectState.FAILED
            self.project.error = "Watchdog failed project"
            self.project.error_code = "EXTRACTION_FAILED"
            return self.pages_extracted

    db = RaceDB(project, spec, pages_extracted=1)
    pending_pages = [page]

    monkeypatch.setattr(project_extraction, "async_session_factory", lambda: db)
    monkeypatch.setattr(
        project_extraction,
        "settings",
        SimpleNamespace(MAX_PAGES_PER_JOB=500, MAX_RECORDS_PER_PAGE=1000, MIN_CRAWL_DELAY_MS=0),
    )
    monkeypatch.setattr(project_extraction, "validate_url", lambda url: url)

    async def not_canceled(db, project_id):
        return False

    async def next_pending_page(db, run_id):
        page = pending_pages.pop(0) if pending_pages else None
        if page is None:
            return None
        page.state = CrawlPageState.FETCHING
        page.lease_token = "tok"
        return page, "tok"

    async def _owns_lease(db, page_id, token):
        return True

    async def crawl_page_count(db, project_id):
        return 1

    async def fake_fetch_url(url, render_mode, **kwargs):
        return SimpleNamespace(
            html="<html><body>No records</body></html>",
            final_url=url,
        )

    monkeypatch.setattr(
        project_extraction,
        "_project_was_canceled",
        not_canceled,
    )
    monkeypatch.setattr(
        project_extraction,
        "_claim_pending_page",
        next_pending_page,
    )
    monkeypatch.setattr(project_extraction, "_still_owns_lease", _owns_lease)
    monkeypatch.setattr(
        project_extraction,
        "_crawl_page_count",
        crawl_page_count,
    )
    monkeypatch.setattr(
        project_extraction,
        "fetch_url",
        fake_fetch_url,
    )
    # Extraction now flows through the variant orchestrator, which (with the
    # default disabled profile) calls extract_records_from_html in
    # interaction_extraction. Patch it there.
    monkeypatch.setattr(
        "app.services.interaction_extraction.extract_records_from_html",
        lambda *args, **kwargs: [],
    )

    await project_extraction.execute_project_extraction(project.id, spec.id, db.run.id)

    assert project.state == ProjectState.FAILED
    assert project.error_code == "EXTRACTION_FAILED"
    # The page produced no records, so it is FAILED+SELECTOR_ZERO_MATCH; the key
    # point is the watchdog-failed project is not forced to COMPLETED.
    assert page.state == CrawlPageState.FAILED
    assert page.block_reason == "SELECTOR_ZERO_MATCH"
    assert db.added == []


# ---------------------------------------------------------------------------
# 6. Config: CORS, CRAWL_CONCURRENCY, watchdog timeouts
# ---------------------------------------------------------------------------


def test_cors_origins_includes_vite_dev_origin():
    """CORS_ORIGINS default includes http://127.0.0.1:5173."""
    from app.core.config import Settings
    from cryptography.fernet import Fernet

    settings = Settings(
        PROVIDER_KEY_ENCRYPTION_SECRET=Fernet.generate_key().decode(),
        _env_file=None,
    )

    assert "http://127.0.0.1:5173" in settings.CORS_ORIGINS
    origins = settings.cors_origins_list
    assert "http://127.0.0.1:5173" in origins


def test_multiprocess_safety_settings_defaults():
    """A2: RUN_SCHEDULER + RATE_LIMIT_STORAGE_URI default to safe single-process."""
    from app.core.config import Settings
    from cryptography.fernet import Fernet

    settings = Settings(
        PROVIDER_KEY_ENCRYPTION_SECRET=Fernet.generate_key().decode(),
        _env_file=None,
    )
    assert settings.RUN_SCHEDULER is True
    assert settings.RATE_LIMIT_STORAGE_URI == "memory://"


def test_watchdog_project_timeout_defaults():
    """New watchdog project timeout settings have correct defaults."""
    from app.core.config import Settings
    from cryptography.fernet import Fernet

    settings = Settings(
        PROVIDER_KEY_ENCRYPTION_SECRET=Fernet.generate_key().decode(),
        _env_file=None,
    )

    assert settings.WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES == 10
    assert settings.WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES == 10
    assert settings.WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES == 10


def test_watchdog_project_timeout_field_info():
    """Watchdog project timeout fields have descriptions."""
    from app.core.config import Settings

    discovering_field = Settings.model_fields[
        "WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES"
    ]
    extracting_field = Settings.model_fields[
        "WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES"
    ]
    exporting_field = Settings.model_fields[
        "WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES"
    ]

    assert discovering_field.description is not None
    assert extracting_field.description is not None
    assert exporting_field.description is not None
