"""Tests for GET/POST/DELETE /jobs and POST /jobs/{id}/cancel."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.v1.endpoints import jobs
from app.models.job import (
    ExtractionMode,
    Job,
    JobState,
    RenderMode,
    WorkflowMode,
)
from app.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(user_id: int = 1) -> User:
    return User(id=user_id, email="user@test.com", hashed_password="hash",
                default_provider_id=None)


def _job(
    job_id: int = 1,
    user_id: int = 1,
    state: JobState = JobState.ANALYSIS_READY,
    url: str = "https://example.com",
    extraction_mode: ExtractionMode = ExtractionMode.STRUCTURED,
    workflow_mode: WorkflowMode = WorkflowMode.GUIDED,
    render_mode: RenderMode = RenderMode.AUTO,
    error: str | None = None,
    analysis: dict | None = None,
) -> Job:
    j = Job(
        id=job_id,
        user_id=user_id,
        state=state,
        url=url,
        extraction_mode=extraction_mode,
        workflow_mode=workflow_mode,
        render_mode=render_mode,
        error=error,
        analysis=analysis,
        warnings=[],
        created_at=datetime.now(timezone.utc),
    )
    return j


class _FakeScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeExecResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalarsResult(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalar_one(self):
        return self._items[0] if self._items else 0


class FakeJobSession:
    def __init__(self, job_list: list[Job]):
        self._jobs = job_list
        self.deleted = []
        self.committed = 0

    async def execute(self, statement):
        return _FakeExecResult(self._jobs)

    async def get(self, model, pk):
        for j in self._jobs:
            if j.id == pk:
                return j
        return None

    async def delete(self, obj):
        if obj in self._jobs:
            self._jobs.remove(obj)
            self.deleted.append(obj)

    async def commit(self):
        self.committed += 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(jobs.router, prefix="/api/v1")
    return application


# ---------------------------------------------------------------------------
# GET /jobs — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_requires_auth(async_client, app):
    response = await async_client.get("/api/v1/jobs")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_jobs_returns_owned_jobs(async_client, app):
    job_list = [_job(job_id=2), _job(job_id=1)]
    session = FakeJobSession(job_list)
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.get("/api/v1/jobs")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["id"] == 2
    assert body[1]["id"] == 1


@pytest.mark.asyncio
async def test_list_jobs_respects_limit(async_client, app):
    session = FakeJobSession([])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.get("/api/v1/jobs?limit=200")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /jobs/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_returns_detail(async_client, app):
    j = _job(job_id=5)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.get("/api/v1/jobs/5")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 5
    assert body["state"] == "ANALYSIS_READY"


@pytest.mark.asyncio
async def test_get_job_404_for_other_user(async_client, app):
    j = _job(job_id=9, user_id=2)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.get("/api/v1/jobs/9")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_job_404_when_not_found(async_client, app):
    session = FakeJobSession([])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.get("/api/v1/jobs/999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /jobs/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_terminal_job_succeeds(async_client, app):
    j = _job(job_id=10, state=JobState.FAILED)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.delete("/api/v1/jobs/10")
    assert response.status_code == 204
    assert len(session._jobs) == 0


@pytest.mark.asyncio
async def test_delete_analysis_ready_job_succeeds(async_client, app):
    j = _job(job_id=11, state=JobState.ANALYSIS_READY)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.delete("/api/v1/jobs/11")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_active_job_returns_400(async_client, app):
    j = _job(job_id=12, state=JobState.ANALYZING)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.delete("/api/v1/jobs/12")
    assert response.status_code == 400
    assert len(session._jobs) == 1


@pytest.mark.asyncio
async def test_delete_job_404_for_other_user(async_client, app):
    j = _job(job_id=13, user_id=2, state=JobState.FAILED)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.delete("/api/v1/jobs/13")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /jobs/{id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_active_job(async_client, app, monkeypatch):
    j = _job(job_id=20, state=JobState.QUEUED)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    from app.services.job_state import JobTransitionResult

    canceled_job = _job(job_id=20, state=JobState.CANCELED)

    async def fake_cancel(job_id, expected_states=None):
        return JobTransitionResult(success=True, job=canceled_job)

    # Patch at the reference site — jobs.py imported the function directly
    monkeypatch.setattr("app.api.v1.endpoints.jobs.transition_job_to_canceled", fake_cancel)

    response = await async_client.post("/api/v1/jobs/20/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "CANCELED"


@pytest.mark.asyncio
async def test_cancel_terminal_job_returns_409(async_client, app):
    j = _job(job_id=21, state=JobState.FAILED)
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.post("/api/v1/jobs/21/cancel")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /jobs — admission errors (no background task execution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_no_provider_returns_409(async_client, app, monkeypatch):
    session = FakeJobSession([])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    from app.services.job_admission import JobAdmissionError, JobAdmissionErrorType

    async def fake_admit(*args, **kwargs):
        return JobAdmissionError(
            error_type=JobAdmissionErrorType.NO_PROVIDER_CONFIGURED,
            message="No provider configured",
        )

    monkeypatch.setattr("app.api.v1.endpoints.jobs.admit_job", fake_admit)

    response = await async_client.post(
        "/api/v1/jobs",
        json={"url": "https://example.com", "extraction_mode": "STRUCTURED",
              "workflow_mode": "GUIDED", "render_mode": "AUTO"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "NO_PROVIDER_CONFIGURED"


@pytest.mark.asyncio
async def test_create_job_active_limit_returns_409(async_client, app, monkeypatch):
    session = FakeJobSession([])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    from app.services.job_admission import JobAdmissionError, JobAdmissionErrorType

    async def fake_admit(*args, **kwargs):
        return JobAdmissionError(
            error_type=JobAdmissionErrorType.ACTIVE_JOB_LIMIT_REACHED,
            message="Too many active jobs",
        )

    monkeypatch.setattr("app.api.v1.endpoints.jobs.admit_job", fake_admit)

    response = await async_client.post(
        "/api/v1/jobs",
        json={"url": "https://example.com", "extraction_mode": "STRUCTURED",
              "workflow_mode": "GUIDED", "render_mode": "AUTO"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "ACTIVE_JOB_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_create_job_success_returns_202(async_client, app, monkeypatch):
    session = FakeJobSession([])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    new_job = _job(job_id=99, state=JobState.QUEUED)

    from app.services.job_admission import JobAdmissionSuccess

    class FakeProvider:
        id = 1

    async def fake_admit(*args, **kwargs):
        return JobAdmissionSuccess(job=new_job, provider_config=FakeProvider())

    monkeypatch.setattr("app.api.v1.endpoints.jobs.admit_job", fake_admit)

    async def fake_execute(*args, **kwargs):
        pass

    monkeypatch.setattr("app.api.v1.endpoints.jobs.execute_job_pipeline", fake_execute)

    response = await async_client.post(
        "/api/v1/jobs",
        json={"url": "https://example.com", "extraction_mode": "STRUCTURED",
              "workflow_mode": "GUIDED", "render_mode": "AUTO"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["id"] == 99
    assert body["state"] == "QUEUED"


# ---------------------------------------------------------------------------
# Rate limit wiring
# ---------------------------------------------------------------------------


def test_create_job_endpoint_has_rate_limit_wiring():
    """POST /jobs must declare `request: Request` — required by SlowAPI."""
    import inspect
    from app.api.v1.endpoints.jobs import create_job

    params = inspect.signature(create_job).parameters
    assert "request" in params, (
        "create_job must declare 'request: Request' for SlowAPI rate limiting"
    )


# ---------------------------------------------------------------------------
# JobListItem warnings contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_includes_warnings(async_client, app):
    """GET /jobs must include warnings in each JobListItem."""
    j = _job(job_id=7)
    j.warnings = ["Selector had low confidence", "JavaScript detected"]
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.get("/api/v1/jobs")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["warnings"] == [
        "Selector had low confidence",
        "JavaScript detected",
    ]


@pytest.mark.asyncio
async def test_list_jobs_warnings_defaults_to_empty_list(async_client, app):
    """GET /jobs must return warnings=[] when job.warnings is None."""
    j = _job(job_id=8)
    j.warnings = None  # type: ignore[assignment]
    session = FakeJobSession([j])
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield session)

    response = await async_client.get("/api/v1/jobs")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["warnings"] == []
