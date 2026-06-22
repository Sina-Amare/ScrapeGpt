"""Tests for the project workflow API."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.v1.endpoints import projects
from app.models.job import (
    CrawlPage,
    Export,
    ExtractedRecord,
    ExtractionMode,
    ExtractionRun,
    ExtractionSpec,
    FrontierPreview,
    PreviewResult,
    Project,
    ProjectState,
    RenderMode,
    WorkflowMode,
)
from app.models.provider_config import ProviderConfig
from app.models.user import User
from app.services.job_admission import JobAdmissionSuccess
from app.services.project_retry import RetryError


def _user(user_id: int = 1) -> User:
    return User(id=user_id, email="user@test.com", hashed_password="hash")


def _project(user_id: int = 1) -> Project:
    return Project(
        id=1,
        user_id=user_id,
        provider_config_id=1,
        url="https://example.com/",
        extraction_mode=ExtractionMode.STRUCTURED,
        workflow_mode=WorkflowMode.GUIDED,
        render_mode=RenderMode.AUTO,
        state=ProjectState.QUEUED,
        warnings=[],
        created_at=datetime.now(timezone.utc),
    )


class _NoRows:
    rowcount = 0

    def scalar_one_or_none(self):
        return None

    def all(self):
        return []

    def scalars(self):
        return self

    def __iter__(self):
        return iter(())


class _Result:
    def scalar_one_or_none(self):
        return None


class FakeProjectSession:
    def __init__(self, project: Project | None = None):
        self.project = project
        self.commits = 0
        self.deleted_tables = []

    async def get(self, model, pk):
        if model is Project and self.project and self.project.id == pk:
            return self.project
        return None

    async def execute(self, statement):
        table = getattr(statement, "table", None)
        if table is not None:
            self.deleted_tables.append(table.name)
        return _NoRows()

    async def scalar(self, statement):
        return 0

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass

    async def commit(self):
        self.commits += 1


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(projects.router, prefix="/api/v1")
    return application


@pytest.mark.asyncio
async def test_list_projects_requires_auth(async_client):
    response = await async_client.get("/api/v1/projects")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_analyze_project_defaults_to_url_only(async_client, app, monkeypatch):
    created = _project()
    provider = ProviderConfig(id=1, user_id=1, name="Default", provider="openai", model="gpt")
    captured = {}

    async def fake_admit(**kwargs):
        captured.update(kwargs)
        return JobAdmissionSuccess(job=created, provider_config=provider)

    async def fake_execute(**kwargs):
        return None

    monkeypatch.setattr("app.api.v1.endpoints.projects.admit_job", fake_admit)
    monkeypatch.setattr("app.api.v1.endpoints.projects.execute_job_pipeline", fake_execute)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(created))

    response = await async_client.post(
        "/api/v1/projects/analyze",
        json={"url": "https://example.com/"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == 1
    assert body["system_state"] == "QUEUED"
    assert body["product_status_label"] == "Analyzing site"
    assert captured["extraction_mode"] == "STRUCTURED"
    assert captured["workflow_mode"] == "GUIDED"
    assert captured["render_mode"] == "AUTO"
    assert captured["provider_config_id"] is None


@pytest.mark.asyncio
async def test_get_project_404_for_other_user(async_client, app):
    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(_project(user_id=2)))

    response = await async_client.get("/api/v1/projects/1")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_terminal_project_removes_project_tree(async_client, app):
    project = _project()
    project.state = ProjectState.COMPLETED
    db = FakeProjectSession(project)
    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield db)

    response = await async_client.delete("/api/v1/projects/1")

    assert response.status_code == 204
    assert db.commits == 1
    assert db.deleted_tables == [
        ExtractedRecord.__tablename__,
        Export.__tablename__,
        FrontierPreview.__tablename__,
        PreviewResult.__tablename__,
        CrawlPage.__tablename__,
        ExtractionRun.__tablename__,
        ExtractionSpec.__tablename__,
        Project.__tablename__,
    ]


@pytest.mark.asyncio
async def test_delete_active_project_returns_400_without_deleting(async_client, app):
    project = _project()
    project.state = ProjectState.EXTRACTING
    db = FakeProjectSession(project)
    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield db)

    response = await async_client.delete("/api/v1/projects/1")

    assert response.status_code == 400
    assert db.commits == 0
    assert db.deleted_tables == []


@pytest.mark.asyncio
async def test_invalid_css_selector_returns_422(async_client, app):
    project = _project()
    project.state = ProjectState.ANALYSIS_READY
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))

    response = await async_client.patch(
        "/api/v1/projects/1/spec",
        json={"fields": [{"name": "title", "selector": "##invalid[unclosed"}]},
    )

    assert response.status_code == 422
    assert "selector" in response.text.lower()


@pytest.mark.asyncio
async def test_zero_preview_records_blocks_extraction(async_client, app, monkeypatch):
    project = _project()
    project.state = ProjectState.PREVIEW_READY

    fake_spec = ExtractionSpec(
        id=1,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[{"name": "title", "selector": "h1", "selected": True}],
        content_config={},
        url_patterns=[],
        page_limit=500,
        export_format="csv",
        created_at=datetime.now(timezone.utc),
    )
    fake_preview = PreviewResult(
        id=1,
        project_id=1,
        spec_id=1,
        sample_records=[],
        warnings=[],
        missing_fields=[],
        quality_summary={"sample_record_count": 0},
        created_at=datetime.now(timezone.utc),
    )

    async def _latest_spec(db, project_id):
        return fake_spec

    async def _latest_preview(db, project_id):
        return fake_preview

    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_spec", _latest_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", _latest_preview)
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))

    response = await async_client.post("/api/v1/projects/1/extract")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "ZERO_PREVIEW_RECORDS"


@pytest.mark.asyncio
async def test_extract_anyway_does_not_bypass_zero_preview_gate(async_client, app, monkeypatch):
    # A *current* zero-record preview means selectors match nothing, so extraction
    # would produce zero rows. extract_anyway must NOT force it (unlike no/stale preview).
    project = _project()
    project.state = ProjectState.PREVIEW_READY

    fake_spec = ExtractionSpec(
        id=1,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[],
        content_config={},
        url_patterns=[],
        page_limit=500,
        export_format="csv",
        created_at=datetime.now(timezone.utc),
    )
    fake_preview = PreviewResult(
        id=1,
        project_id=1,
        spec_id=1,
        sample_records=[],
        warnings=[],
        missing_fields=[],
        quality_summary={"sample_record_count": 0},
        created_at=datetime.now(timezone.utc),
    )

    async def _latest_spec(db, project_id):
        return fake_spec

    async def _latest_preview(db, project_id):
        return fake_preview

    async def _start_extraction(db, project, spec, *, allow_unconfirmed=False):
        project.state = ProjectState.DISCOVERING

    async def _ensure_default_spec(db, project):
        return fake_spec

    async def _noop_execute(project_id, spec_id):
        pass

    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_spec", _latest_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", _latest_preview)
    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.start_project_extraction", _start_extraction
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.ensure_default_spec", _ensure_default_spec
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.execute_project_extraction", _noop_execute
    )
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))

    response = await async_client.post(
        "/api/v1/projects/1/extract",
        json={"extract_anyway": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "ZERO_PREVIEW_RECORDS"


def _failed_project(*, with_analysis: bool = False) -> Project:
    project = _project()
    project.state = ProjectState.FAILED
    project.error = "Something went wrong"
    project.error_code = "LLM_ANALYSIS_FAILED"
    if with_analysis:
        project.analysis = {"candidate_fields": []}
    return project


def _fake_spec(spec_id: int = 1, *, updated_at: datetime | None = None) -> ExtractionSpec:
    return ExtractionSpec(
        id=spec_id,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[],
        content_config={},
        url_patterns=[],
        page_limit=500,
        export_format="csv",
        created_at=datetime.now(timezone.utc),
        updated_at=updated_at,
    )


def _fake_preview(*, spec_id: int = 1, created_at: datetime | None = None) -> PreviewResult:
    return PreviewResult(
        id=1,
        project_id=1,
        spec_id=spec_id,
        sample_records=[{"title": "test"}],
        warnings=[],
        missing_fields=[],
        quality_summary={"sample_record_count": 1},
        created_at=created_at or datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_retry_analysis_failure_requeues(async_client, app, monkeypatch):
    """FAILED project with no analysis resets to QUEUED and re-queues the pipeline."""
    project = _failed_project(with_analysis=False)
    fake_provider = ProviderConfig(id=1, user_id=1, name="Default", provider="openai", model="gpt")

    executed = []

    async def _fake_execute(*, job_id, provider_config_id):
        executed.append((job_id, provider_config_id))

    async def _fake_resolve(db, user, provider_config_id):
        return fake_provider

    monkeypatch.setattr("app.services.project_retry.resolve_provider_for_user", _fake_resolve)
    monkeypatch.setattr("app.api.v1.endpoints.projects.execute_job_pipeline", _fake_execute)
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))

    response = await async_client.post("/api/v1/projects/1/retry")

    assert response.status_code == 200
    assert response.json()["system_state"] == "QUEUED"
    assert len(executed) == 1


@pytest.mark.asyncio
async def test_retry_extraction_failure_with_fresh_preview(async_client, app, monkeypatch):
    """FAILED project with analysis + fresh matching preview resets to PREVIEW_READY."""
    project = _failed_project(with_analysis=True)
    project.error_code = "ALL_PAGES_FAILED"
    spec_time = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    preview_time = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    spec = _fake_spec(updated_at=spec_time)
    preview = _fake_preview(spec_id=spec.id, created_at=preview_time)

    async def _latest_spec(db, project_id):
        return spec

    async def _latest_preview(db, project_id):
        return preview

    async def _ensure_spec(db, project):
        return spec

    monkeypatch.setattr("app.services.project_retry.latest_spec", _latest_spec)
    monkeypatch.setattr("app.services.project_retry.latest_preview", _latest_preview)
    # _project_response calls ensure_default_spec and latest_preview; patch both.
    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", _ensure_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", _latest_preview)
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))

    response = await async_client.post("/api/v1/projects/1/retry")

    assert response.status_code == 200
    assert response.json()["system_state"] == "PREVIEW_READY"


@pytest.mark.asyncio
async def test_retry_extraction_failure_stale_preview(async_client, app, monkeypatch):
    """FAILED project with analysis but stale preview resets to ANALYSIS_READY."""
    project = _failed_project(with_analysis=True)
    project.error_code = "NO_RECORDS_EXTRACTED"
    spec_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # spec updated AFTER preview
    preview_time = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    spec = _fake_spec(updated_at=spec_time)
    preview = _fake_preview(spec_id=spec.id, created_at=preview_time)

    async def _latest_spec(db, project_id):
        return spec

    async def _latest_preview(db, project_id):
        return preview

    async def _ensure_spec(db, project):
        return spec

    monkeypatch.setattr("app.services.project_retry.latest_spec", _latest_spec)
    monkeypatch.setattr("app.services.project_retry.latest_preview", _latest_preview)
    # _project_response calls ensure_default_spec and latest_preview; patch both.
    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", _ensure_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", _latest_preview)
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))

    response = await async_client.post("/api/v1/projects/1/retry")

    assert response.status_code == 200
    assert response.json()["system_state"] == "ANALYSIS_READY"


@pytest.mark.asyncio
async def test_retry_non_failed_project_returns_409(async_client, app):
    """Retrying a non-FAILED project returns 409."""
    project = _project()
    project.state = ProjectState.COMPLETED
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))

    response = await async_client.post("/api/v1/projects/1/retry")

    assert response.status_code == 409


class _DeleteFailingSession(FakeProjectSession):
    """Session that raises on the first DELETE statement to simulate a DB error."""

    async def execute(self, statement):
        if getattr(statement, "table", None) is not None:
            raise RuntimeError("deadlock detected — row lock held by background task")
        return await super().execute(statement)


@pytest.mark.asyncio
async def test_delete_db_exception_returns_500_with_message(async_client, app):
    project = _project()
    project.state = ProjectState.COMPLETED
    db = _DeleteFailingSession(project)
    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield db)

    response = await async_client.delete("/api/v1/projects/1")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "lock" in detail.lower() or "background" in detail.lower()


# ---------------------------------------------------------------------------
# Retry endpoint (regression: MissingGreenlet 500 + provider override)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_failed_project_returns_200_and_passes_provider(async_client, app, monkeypatch):
    project = _project()
    project.state = ProjectState.FAILED
    provider = ProviderConfig(id=2, user_id=1, name="alt", provider="openai", model="gpt")
    captured = {}

    async def fake_retry(db, proj, user, provider_config_id=None):
        captured["provider_config_id"] = provider_config_id
        proj.state = ProjectState.QUEUED
        return proj, provider

    async def fake_execute(**kwargs):
        return None

    async def fake_record(*args, **kwargs):
        return None

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))
    monkeypatch.setattr("app.api.v1.endpoints.projects.retry_failed_project", fake_retry)
    monkeypatch.setattr("app.api.v1.endpoints.projects.execute_job_pipeline", fake_execute)
    monkeypatch.setattr("app.api.v1.endpoints.projects.record_project_event", fake_record)

    response = await async_client.post(
        "/api/v1/projects/1/retry", json={"provider_config_id": 2}
    )

    assert response.status_code == 200
    assert response.json()["system_state"] == "QUEUED"
    # The provider override is threaded into the retry service.
    assert captured["provider_config_id"] == 2


@pytest.mark.asyncio
async def test_retry_without_body_works(async_client, app, monkeypatch):
    project = _project()
    project.state = ProjectState.FAILED

    async def fake_retry(db, proj, user, provider_config_id=None):
        captured_none.append(provider_config_id)
        proj.state = ProjectState.ANALYSIS_READY
        return proj, None  # analysis preserved: no provider, no re-queue

    captured_none: list = []
    async def fake_record(*args, **kwargs):
        return None

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))
    monkeypatch.setattr("app.api.v1.endpoints.projects.retry_failed_project", fake_retry)
    monkeypatch.setattr("app.api.v1.endpoints.projects.record_project_event", fake_record)

    response = await async_client.post("/api/v1/projects/1/retry")

    assert response.status_code == 200
    assert captured_none == [None]


@pytest.mark.asyncio
async def test_retry_non_failed_returns_409(async_client, app, monkeypatch):
    project = _project()  # QUEUED, not FAILED

    async def fake_retry(db, proj, user, provider_config_id=None):
        raise RetryError("Only FAILED projects can be retried.", "NOT_FAILED")

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeProjectSession(project))
    monkeypatch.setattr("app.api.v1.endpoints.projects.retry_failed_project", fake_retry)

    response = await async_client.post("/api/v1/projects/1/retry")

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "NOT_FAILED"


# --- Markdown (.md) export -------------------------------------------------


def test_markdown_export_joins_content_records_as_documents():
    """CONTENT rows export as readable Markdown documents: each prefixed with its
    source URL and separated by a horizontal rule, with the Markdown body intact
    (so the downloaded .md mirrors the in-app preview)."""
    rows = [
        {"source_url": "https://e.com/a", "content": "# Alpha\n\nFirst body."},
        {"source_url": "https://e.com/b", "content": "# Beta\n\n```py\nx = 1\n```"},
    ]
    out = projects._markdown_export(rows)
    assert "[https://e.com/a](https://e.com/a)" in out
    assert "[https://e.com/b](https://e.com/b)" in out
    assert "# Alpha" in out and "# Beta" in out
    assert "```py\nx = 1\n```" in out          # code fence preserved verbatim
    assert "\n---\n" in out                     # records separated by a rule


def test_markdown_export_falls_back_to_table_for_structured_rows():
    """STRUCTURED rows (no ``content`` field) export as a GFM table in spec order,
    with pipe characters in cells escaped so the table never breaks."""
    rows = [
        {"Title": "A | B", "Price": "10", "source_url": "https://e.com/1"},
        {"Title": "C", "Price": "20", "source_url": "https://e.com/2"},
    ]
    out = projects._markdown_export(rows, field_order=["Title", "Price"])
    lines = out.strip().splitlines()
    assert lines[0] == "| Title | Price | source_url |"
    assert lines[1] == "| --- | --- | --- |"
    assert "A \\| B" in lines[2]                 # pipe escaped, table intact
    assert "https://e.com/2" in lines[3]


def test_markdown_export_empty_is_blank():
    assert projects._markdown_export([]) == ""
