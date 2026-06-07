"""Tests for the project workflow API."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.v1.endpoints import projects
from app.models.job import ExtractionMode, Project, ProjectState, RenderMode, WorkflowMode
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
    def scalar_one_or_none(self):
        return None


class _Result:
    def scalar_one_or_none(self):
        return None


class FakeProjectSession:
    def __init__(self, project: Project | None = None):
        self.project = project
        self.commits = 0

    async def get(self, model, pk):
        if model is Project and self.project and self.project.id == pk:
            return self.project
        return None

    async def execute(self, statement):
        return _NoRows()

    async def scalar(self, statement):
        return 0

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj):
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
