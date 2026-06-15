"""Phase 2 API tests: interaction detect endpoint, spec round-trip, export order."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.v1.endpoints import projects
from app.models.job import (
    ExtractionMode,
    ExtractionSpec,
    Project,
    ProjectState,
    RenderMode,
    WorkflowMode,
)
from app.models.user import User


def _user() -> User:
    return User(id=1, email="user@test.com", hashed_password="hash")


def _project() -> Project:
    return Project(
        id=1,
        user_id=1,
        provider_config_id=1,
        url="https://example.com/",
        extraction_mode=ExtractionMode.STRUCTURED,
        workflow_mode=WorkflowMode.GUIDED,
        render_mode=RenderMode.AUTO,
        state=ProjectState.ANALYSIS_READY,
        warnings=[],
        created_at=datetime.now(timezone.utc),
    )


def _spec() -> ExtractionSpec:
    return ExtractionSpec(
        id=10,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[{"name": "Title", "selected": True}],
        content_config={},
        url_patterns=[],
        page_limit=100,
        export_format="csv",
        crawl_scope=None,
        interaction_profile={},
    )


class _NoRows:
    def scalar_one_or_none(self):
        return None

    def __iter__(self):
        return iter(())


class FakeSession:
    def __init__(self, project, spec):
        self.project = project
        self.spec = spec

    async def get(self, model, pk):
        if model is Project and self.project.id == pk:
            return self.project
        if model is ExtractionSpec and self.spec.id == pk:
            return self.spec
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
        pass


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(projects.router, prefix="/api/v1")
    return application


@pytest.mark.asyncio
async def test_detect_interactions_persists_disabled_draft(async_client, app, monkeypatch):
    project = _project()
    spec = _spec()

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_fetch_url(url, render_mode, **kwargs):
        html = (
            '<div class="toggle"><button class="active">Metric</button>'
            "<button>Imperial</button></div>"
        )
        return SimpleNamespace(html=html, final_url=url)

    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec
    )
    monkeypatch.setattr("app.api.v1.endpoints.projects.fetch_url", fake_fetch_url)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project, spec))

    response = await async_client.post("/api/v1/projects/1/interactions/detect")
    assert response.status_code == 200
    body = response.json()
    profile = body["interaction_profile"]
    assert profile["enabled"] is False  # detection never auto-enables
    keys = {g["metadata_key"] for g in profile["groups"]}
    assert "unit_system" in keys
    # persisted on the spec object
    assert spec.interaction_profile["groups"]


@pytest.mark.asyncio
async def test_update_spec_saves_interaction_profile(async_client, app, monkeypatch):
    project = _project()
    spec = _spec()

    async def fake_ensure_default_spec(db, proj):
        return spec

    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec
    )

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project, spec))

    payload = {
        "interaction_profile": {
            "enabled": True,
            "max_variant_combinations": 12,
            "groups": [
                {
                    "label": "Serving basis",
                    "metadata_key": "serving_basis",
                    "execution": "deterministic",
                    "options": [
                        {"id": "a", "label": "per 100 g", "selected": True,
                         "field_selectors": {"Calories": "td:nth-of-type(3)"}, "recipe": []},
                        {"id": "b", "label": "per serving", "selected": True,
                         "field_selectors": {"Calories": "td:nth-of-type(5)"}, "recipe": []},
                    ],
                }
            ],
        }
    }
    response = await async_client.patch("/api/v1/projects/1/spec", json=payload)
    assert response.status_code == 200
    assert spec.interaction_profile["enabled"] is True
    assert spec.interaction_profile["groups"][0]["metadata_key"] == "serving_basis"


@pytest.mark.asyncio
async def test_update_spec_rejects_bad_execution(async_client, app, monkeypatch):
    project = _project()
    spec = _spec()

    async def fake_ensure_default_spec(db, proj):
        return spec

    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec
    )
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project, spec))

    payload = {
        "interaction_profile": {
            "enabled": True,
            "groups": [
                {"label": "X", "metadata_key": "x", "execution": "MAGIC", "options": []}
            ],
        }
    }
    response = await async_client.patch("/api/v1/projects/1/spec", json=payload)
    assert response.status_code == 422


def test_spec_field_order_appends_variant_metadata_when_enabled():
    spec = SimpleNamespace(
        fields=[
            {"name": "Food", "selected": True},
            {"name": "Calories", "selected": True},
        ],
        interaction_profile={
            "enabled": True,
            "groups": [
                {"label": "Serving basis", "metadata_key": "serving_basis",
                 "execution": "deterministic",
                 "options": [{"id": "a", "label": "per 100 g", "selected": True}]},
            ],
        },
    )
    order = projects._spec_field_order(spec)
    assert order[:2] == ["Food", "Calories"]
    assert "interaction_variant_id" in order
    assert "serving_basis" in order
    # spec fields come before metadata columns
    assert order.index("Calories") < order.index("interaction_variant_id")


def test_spec_field_order_ignores_disabled_profile():
    spec = SimpleNamespace(
        fields=[{"name": "Food", "selected": True}],
        interaction_profile={"enabled": False, "groups": []},
    )
    assert projects._spec_field_order(spec) == ["Food"]
