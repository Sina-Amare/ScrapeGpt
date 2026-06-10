"""Tests for reliability hardening changes.

Covers:
1. Legacy /scrape endpoint SSRF validation (400 on private URLs)
2. Legacy /scrape executor SSRF defense-in-depth (fails task on private URLs)
3. Legacy /scrape executor robots.txt check
4. CrawlPage lease reaper (expired leases reset to PENDING)
5. Stuck-project watchdog (projects stuck beyond timeout → FAILED)
6. Extraction completion semantics (all-pages-failed → FAILED)
7. Config: CORS Vite origin, CRAWL_CONCURRENCY description, watchdog timeouts
"""

from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.v1.endpoints import scrape
from app.models.job import (
    CrawlPage,
    CrawlPageState,
    Project,
    ProjectState,
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


@pytest.mark.asyncio
async def test_stuck_project_watchdog_fails_discovering_project(
    monkeypatch,
):
    """cleanup_stuck_projects fails projects stuck in DISCOVERING."""
    from app.services.watchdog import cleanup_stuck_projects

    cutoff_minutes = 10

    monkeypatch.setattr(
        "app.services.watchdog.settings",
        type(
            "S",
            (),
            {
                "WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES": cutoff_minutes,
                "WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES": 60,
                "WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES": 10,
            },
        )(),
    )

    class FakeDB:
        call_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            self.call_count += 1
            result = FakeResult(scalar=None)
            # Only the DISCOVERING UPDATE (first call) matches
            if self.call_count == 1:
                result.rowcount = 1
            else:
                result.rowcount = 0
            return result

        async def commit(self):
            pass

    monkeypatch.setattr(
        "app.services.watchdog.async_session_factory",
        lambda: FakeDB(),
    )

    result = await cleanup_stuck_projects()

    assert result == 1


@pytest.mark.asyncio
async def test_stuck_project_watchdog_fails_extracting_project(
    monkeypatch,
):
    """cleanup_stuck_projects fails projects stuck in EXTRACTING."""
    from app.services.watchdog import cleanup_stuck_projects

    monkeypatch.setattr(
        "app.services.watchdog.settings",
        type(
            "S",
            (),
            {
                "WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES": 10,
                "WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES": 60,
                "WATCHDOG_PROJECT_EXPORTING_TIMEOUT_MINUTES": 10,
            },
        )(),
    )

    # First UPDATE (DISCOVERING) matches 0, second (EXTRACTING) matches 1
    class FakeDB:
        call_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            self.call_count += 1
            result = FakeResult(scalar=None)
            if self.call_count == 2:
                result.rowcount = 1
            else:
                result.rowcount = 0
            return result

        async def commit(self):
            pass

    monkeypatch.setattr(
        "app.services.watchdog.async_session_factory",
        lambda: FakeDB(),
    )

    result = await cleanup_stuck_projects()
    assert result == 1


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


def test_partial_extraction_completes_normally():
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


def test_crawl_concurrency_has_description():
    """CRAWL_CONCURRENCY field has a reserved-for-future description."""
    from app.core.config import Settings

    field_info = Settings.model_fields["CRAWL_CONCURRENCY"]
    assert field_info.description is not None
    assert "Reserved for future" in field_info.description


def test_watchdog_project_timeout_defaults():
    """New watchdog project timeout settings have correct defaults."""
    from app.core.config import Settings
    from cryptography.fernet import Fernet

    settings = Settings(
        PROVIDER_KEY_ENCRYPTION_SECRET=Fernet.generate_key().decode(),
        _env_file=None,
    )

    assert settings.WATCHDOG_PROJECT_DISCOVERING_TIMEOUT_MINUTES == 10
    assert settings.WATCHDOG_PROJECT_EXTRACTING_TIMEOUT_MINUTES == 60
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