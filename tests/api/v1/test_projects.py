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
async def test_extract_anyway_bypasses_zero_preview_gate(async_client, app, monkeypatch):
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

    assert response.status_code == 200
    assert response.json()["system_state"] == "DISCOVERING"


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
