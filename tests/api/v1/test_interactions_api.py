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
    # Purely-interactive toggle (no static parallel columns) stays opt-in.
    assert profile["enabled"] is False
    keys = {g["metadata_key"] for g in profile["groups"]}
    assert "unit_system" in keys
    # persisted on the spec object
    assert spec.interaction_profile["groups"]


@pytest.mark.asyncio
async def test_detect_interactions_auto_enables_parallel_columns(async_client, app, monkeypatch):
    """Detection that finds real parallel-column structure (a deterministic /
    mixed group) AUTO-ENABLES the profile, so the preview and extract immediately
    show every variant instead of only the default column — fixing the recurring
    'it only shows per-100 g' confusion. (Purely-interactive toggles stay
    opt-in: see the test above.)"""
    project = _project()
    spec = ExtractionSpec(
        id=10,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[
            {"name": "Food", "user_label": "Food",
             "selector": "td:nth-child(1)", "type": "string", "selected": True},
            {"name": "Calories (per 100 g)", "user_label": "Calories (per 100 g)",
             "selector": "td:nth-child(3)", "type": "number", "selected": True},
            {"name": "Calories (per serving)", "user_label": "Calories (per serving)",
             "selector": "td:nth-child(5)", "type": "number", "selected": True},
        ],
        content_config={},
        url_patterns=[],
        page_limit=100,
        export_format="csv",
        crawl_scope=None,
        interaction_profile={},
    )

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_fetch_url(url, render_mode, **kwargs):
        html = ("<table><tr><td>Beef</td><td>100 g</td><td>156</td>"
                "<td>100 g</td><td>265</td></tr></table>")
        return SimpleNamespace(html=html, final_url=url)

    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec
    )
    monkeypatch.setattr("app.api.v1.endpoints.projects.fetch_url", fake_fetch_url)
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project, spec))

    response = await async_client.post("/api/v1/projects/1/interactions/detect")
    assert response.status_code == 200
    profile = response.json()["interaction_profile"]
    assert profile["enabled"] is True  # parallel columns -> auto-enabled
    assert {g["execution"] for g in profile["groups"]} & {"deterministic", "mixed"}


@pytest.mark.asyncio
async def test_auto_enable_deselects_interactive_secondary_axis(async_client, app, monkeypatch):
    """Auto-enable selects only the primary data axis (deterministic/mixed). A
    purely-interactive *display* toggle (Metric/Imperial) is left DESELECTED so
    it can't cross-multiply the output into meaningless rows like
    'per 100 g x Imperial'. Regression for that exact bad preview."""
    project = _project()
    spec = ExtractionSpec(
        id=10,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[
            {"name": "Food", "user_label": "Food",
             "selector": "td:nth-child(1)", "type": "string", "selected": True},
            {"name": "Calories (per 100 g)", "user_label": "Calories (per 100 g)",
             "selector": "td:nth-child(2)", "type": "number", "selected": True},
            {"name": "Calories (per serving)", "user_label": "Calories (per serving)",
             "selector": "td:nth-child(3)", "type": "number", "selected": True},
        ],
        content_config={},
        url_patterns=[],
        page_limit=100,
        export_format="csv",
        crawl_scope=None,
        interaction_profile={},
    )

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_fetch_url(url, render_mode, **kwargs):
        html = ("<table><tr><td>Beef</td><td>156</td><td>265</td></tr></table>"
                '<div class="toggle"><button class="active">Metric</button>'
                "<button>Imperial</button></div>")
        return SimpleNamespace(html=html, final_url=url)

    monkeypatch.setattr(
        "app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec
    )
    monkeypatch.setattr("app.api.v1.endpoints.projects.fetch_url", fake_fetch_url)
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project, spec))

    response = await async_client.post("/api/v1/projects/1/interactions/detect")
    assert response.status_code == 200
    profile = response.json()["interaction_profile"]
    assert profile["enabled"] is True
    by_exec = {g["metadata_key"]: g for g in profile["groups"]}
    # primary axis (the calorie columns) stays selected...
    primary = next(g for g in profile["groups"]
                   if g["execution"] in ("deterministic", "mixed"))
    assert all(o["selected"] for o in primary["options"])
    # ...the Metric/Imperial display toggle is deselected by default.
    unit = by_exec.get("unit_system")
    assert unit is not None and unit["execution"] == "interactive"
    assert all(o["selected"] is False for o in unit["options"])


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
async def test_update_spec_saves_mixed_interaction_profile(async_client, app, monkeypatch):
    """A 'mixed' group (static columns + a browser toggle on the same axis) must
    round-trip through the spec-update schema. Its options carry BOTH a recipe
    and field_selectors — regression for the save path rejecting 'mixed'."""
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
                    "execution": "mixed",
                    "options": [
                        {"id": "a", "label": "Show per 100 g", "selected": True,
                         "field_selectors": {"Calories": "td:nth-of-type(3)"},
                         "recipe": []},
                        {"id": "b", "label": "Show per serving", "selected": True,
                         "field_selectors": {"Calories": "td:nth-of-type(3)"},
                         "recipe": [{"action": "click", "by": "text",
                                     "value": "Show per serving"}]},
                    ],
                }
            ],
        }
    }
    response = await async_client.patch("/api/v1/projects/1/spec", json=payload)
    assert response.status_code == 200
    saved = spec.interaction_profile["groups"][0]
    assert saved["execution"] == "mixed"
    # the browser option keeps both its recipe and its column selectors
    assert saved["options"][1]["recipe"][0]["value"] == "Show per serving"
    assert saved["options"][1]["field_selectors"]["Calories"] == "td:nth-of-type(3)"


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
    # only the per-axis metadata column is exported (no generic id/label)
    assert "serving_basis" in order
    assert "interaction_variant_id" not in order
    assert "interaction_variant_label" not in order
    # spec fields come before metadata columns
    assert order.index("Calories") < order.index("serving_basis")


def test_spec_field_order_ignores_disabled_profile():
    spec = SimpleNamespace(
        fields=[{"name": "Food", "selected": True}],
        interaction_profile={"enabled": False, "groups": []},
    )
    assert projects._spec_field_order(spec) == ["Food"]


# ---------------------------------------------------------------------------
# AZ1: the interaction endpoints are owner-checked (404 on mismatch, do not
# reveal existence). Invariant #1 applied to the interaction API.
# ---------------------------------------------------------------------------


def _other_users_project() -> Project:
    project = _project()
    project.user_id = 2  # owned by someone other than the caller (id=1)
    return project


@pytest.mark.asyncio
async def test_detect_interactions_404_for_other_user(async_client, app):
    """User A cannot run interaction detection on User B's project."""
    app.dependency_overrides[deps.get_current_user] = lambda: _user()  # id=1
    app.dependency_overrides[deps.get_db] = lambda: (
        yield FakeSession(_other_users_project(), _spec())
    )

    response = await async_client.post("/api/v1/projects/1/interactions/detect")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_spec_interactions_404_for_other_user(async_client, app):
    """User A cannot edit the interaction profile on User B's project."""
    app.dependency_overrides[deps.get_current_user] = lambda: _user()  # id=1
    app.dependency_overrides[deps.get_db] = lambda: (
        yield FakeSession(_other_users_project(), _spec())
    )

    payload = {"interaction_profile": {"enabled": True, "groups": []}}
    response = await async_client.patch("/api/v1/projects/1/spec", json=payload)
    assert response.status_code == 404
