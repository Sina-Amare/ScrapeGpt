"""Step 3 API contract tests: frontier preview, records-page, scope enforcement, quality exposure."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.v1.endpoints import projects
from app.models.job import (
    ExtractionMode,
    ExtractionSpec,
    FrontierPreview,
    ExtractedRecord,
    Project,
    ProjectState,
    RenderMode,
    WorkflowMode,
)
from app.models.user import User
from app.services.crawl_scope import ScopeConfirmationError


def _user(user_id: int = 1) -> User:
    return User(id=user_id, email="user@test.com", hashed_password="hash")


def _project(user_id: int = 1, state: ProjectState = ProjectState.ANALYSIS_READY) -> Project:
    return Project(
        id=1,
        user_id=user_id,
        provider_config_id=1,
        url="https://example.com/",
        extraction_mode=ExtractionMode.STRUCTURED,
        workflow_mode=WorkflowMode.GUIDED,
        render_mode=RenderMode.AUTO,
        state=state,
        warnings=[],
        created_at=datetime.now(timezone.utc),
    )


def _spec(crawl_scope: dict | None = None, quality_summary: dict | None = None) -> ExtractionSpec:
    spec = ExtractionSpec(
        id=10,
        project_id=1,
        mode=ExtractionMode.STRUCTURED,
        fields=[{"name": "Title", "selected": True}],
        content_config={},
        url_patterns=[],
        page_limit=100,
        export_format="csv",
        crawl_scope=crawl_scope,
        quality_summary=quality_summary,
    )
    return spec


def _frontier_preview() -> FrontierPreview:
    return FrontierPreview(
        id=5,
        project_id=1,
        spec_id=10,
        scope_hash="abc123",
        included_urls=[
            {
                "url": "https://example.com/",
                "normalized_url": "https://example.com/",
                "source_url": None,
                "depth": 0,
                "decision": "included",
                "role": "seed",
                "reason_code": "SEED_URL",
                "reason": "Seed URL.",
                "confidence": None,
                "link_text": None,
            }
        ],
        excluded_urls=[
            {
                "url": "https://example.com/about",
                "normalized_url": "https://example.com/about",
                "source_url": "https://example.com/",
                "depth": 1,
                "decision": "excluded",
                "role": None,
                "reason_code": "CURRENT_PAGE_SCOPE",
                "reason": "Mode is CURRENT_PAGE: only the seed URL is crawled.",
                "confidence": None,
                "link_text": "About",
            }
        ],
        estimated_page_count=1,
        warnings=[],
        quality_summary={"included_count": 1, "excluded_count": 1, "unrelated_same_origin_count": 1, "source": "seed_page_frontier_preview"},
        created_at=datetime.now(timezone.utc),
    )


class _NoRows:
    def scalar_one_or_none(self):
        return None

    def __iter__(self):
        return iter(())

    def scalars(self):
        return self

    def all(self):
        return []


class FakeSession:
    """Minimal async session stub for Step 3 tests."""

    def __init__(self, project: Project | None = None, spec: ExtractionSpec | None = None):
        self.project = project
        self.spec = spec
        self.commits = 0
        self.added = []

    async def get(self, model, pk):
        if model is Project and self.project and self.project.id == pk:
            return self.project
        if model is ExtractionSpec and self.spec and self.spec.id == pk:
            return self.spec
        return None

    async def execute(self, statement):
        return _NoRows()

    async def scalar(self, statement):
        return 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def commit(self):
        self.commits += 1

    async def delete(self, obj):
        pass


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(projects.router, prefix="/api/v1")
    return application


# ─── Spec update: crawl_scope field ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_spec_saves_crawl_scope(async_client, app, monkeypatch):
    """PATCH /spec with crawl_scope payload persists the scope to the spec."""
    project = _project()
    scope = {
        "version": 1,
        "mode": "PAGINATION",
        "status": "USER_CONFIRMED",
        "seed_url": "https://example.com/",
        "max_pages": 25,
        "max_depth": None,
        "include_patterns": [],
        "exclude_patterns": [],
        "pagination": {},
        "link_rules": [],
        "ai_recommendation": None,
        "user_confirmed_at": None,
    }
    spec = _spec()
    saved_scope = {}

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_latest_spec(db, project_id):
        return spec

    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_spec", fake_latest_spec)

    db = FakeSession(project=project, spec=spec)

    def fake_refresh_scope(obj):
        if isinstance(obj, ExtractionSpec):
            saved_scope.update(obj.crawl_scope or {})

    db.refresh = AsyncMock(side_effect=fake_refresh_scope)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield db)

    response = await async_client.patch(
        "/api/v1/projects/1/spec",
        json={"crawl_scope": scope},
    )

    assert response.status_code == 200
    # The spec object must have had crawl_scope set before commit
    assert spec.crawl_scope is not None
    assert spec.crawl_scope["mode"] == "PAGINATION"
    assert spec.crawl_scope["status"] == "USER_CONFIRMED"


@pytest.mark.asyncio
async def test_update_spec_seeds_collection_patterns_from_recommendation(async_client, app, monkeypatch):
    project = _project()
    scope = {
        "version": 1,
        "mode": "COLLECTION",
        "status": "USER_CONFIRMED",
        "seed_url": "https://example.com/food/beef",
        "max_pages": 25,
        "max_depth": None,
        "include_patterns": [],
        "exclude_patterns": [],
        "pagination": {},
        "link_rules": [],
        "ai_recommendation": {
            "recommended_mode": "COLLECTION",
            "confidence": 0.7,
            "warnings": [],
            "evidence": [],
            "suggested_include_patterns": ["/food/*"],
        },
        "user_confirmed_at": None,
    }
    spec = _spec()

    async def fake_ensure_default_spec(db, proj):
        return spec

    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project, spec=spec))

    response = await async_client.patch(
        "/api/v1/projects/1/spec",
        json={"crawl_scope": scope},
    )

    assert response.status_code == 200
    assert spec.crawl_scope["mode"] == "COLLECTION"
    assert spec.crawl_scope["include_patterns"] == ["/food/*"]
    assert spec.crawl_scope["max_depth"] == 1


@pytest.mark.asyncio
async def test_update_spec_rejects_invalid_crawl_scope_mode(async_client, app, monkeypatch):
    """PATCH /spec with an unknown crawl_scope.mode returns 422."""
    project = _project()
    spec = _spec()

    async def fake_ensure_default_spec(db, proj):
        return spec

    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project, spec=spec))

    response = await async_client.patch(
        "/api/v1/projects/1/spec",
        json={"crawl_scope": {"mode": "INVALID_MODE", "status": "USER_CONFIRMED"}},
    )

    assert response.status_code == 422


# ─── Scope confirmation enforcement ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_returns_409_when_scope_not_confirmed(async_client, app, monkeypatch):
    """POST /extract returns 409 with actionable error_code when scope is unconfirmed."""
    project = _project(state=ProjectState.PREVIEW_READY)
    spec = _spec(crawl_scope={"mode": "PAGINATION", "status": "AI_SUGGESTED"})
    spec.created_at = datetime.now(timezone.utc)

    async def fake_latest_spec(db, project_id):
        return spec

    async def fake_latest_preview(db, project_id):
        m = MagicMock()
        m.spec_id = spec.id
        m.created_at = datetime.now(timezone.utc)
        m.sample_records = [{"Title": "Example"}]  # non-empty to pass ZERO_PREVIEW_RECORDS gate
        m.quality_summary = {
            "selected_field_count": len(spec.fields),
        }
        return m

    async def fake_start_extraction(db, project, spec, *, allow_unconfirmed=False):
        raise ScopeConfirmationError(spec.crawl_scope, code="SCOPE_NOT_CONFIRMED")

    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_spec", fake_latest_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", fake_latest_preview)
    monkeypatch.setattr("app.api.v1.endpoints.projects.start_project_extraction", fake_start_extraction)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.post("/api/v1/projects/1/extract", json={})

    assert response.status_code == 409
    body = response.json()
    assert "detail" in body
    detail = body["detail"]
    assert detail["error_code"] == "SCOPE_NOT_CONFIRMED"
    assert "message" in detail


@pytest.mark.asyncio
async def test_extract_proceeds_when_scope_confirmed(async_client, app, monkeypatch):
    """POST /extract succeeds (202/200) when scope is USER_CONFIRMED."""
    project = _project(state=ProjectState.PREVIEW_READY)
    spec = _spec(crawl_scope={"mode": "PAGINATION", "status": "USER_CONFIRMED"})

    async def fake_latest_spec(db, project_id):
        return spec

    async def fake_latest_preview(db, project_id):
        return None  # no preview; extract_anyway bypasses the preview gate

    async def fake_start_extraction(db, project, spec, *, allow_unconfirmed=False):
        return SimpleNamespace(id=4242)  # the new run

    async def fake_execute(project_id, spec_id, run_id=None):
        pass

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_latest_fp(db, project_id):
        return None

    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_spec", fake_latest_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", fake_latest_preview)
    monkeypatch.setattr("app.api.v1.endpoints.projects.start_project_extraction", fake_start_extraction)
    monkeypatch.setattr("app.api.v1.endpoints.projects.execute_project_extraction", fake_execute)
    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_frontier_preview", fake_latest_fp)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    # extract_anyway=True lets us skip the preview prerequisite check
    response = await async_client.post("/api/v1/projects/1/extract", json={"extract_anyway": True})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_extract_returns_409_when_already_running(async_client, app, monkeypatch):
    """A second /extract while a run is active returns 409 EXTRACTION_ALREADY_RUNNING."""
    from app.services.project_extraction import ExtractionAlreadyRunningError

    project = _project(state=ProjectState.PREVIEW_READY)
    spec = _spec(crawl_scope={"mode": "PAGINATION", "status": "USER_CONFIRMED"})

    async def fake_latest_spec(db, project_id):
        return spec

    async def fake_latest_preview(db, project_id):
        m = MagicMock()
        m.sample_records = [{"id": 1}]
        m.created_at = datetime.now(timezone.utc)
        m.spec_id = spec.id
        return m

    async def fake_start(db, project, spec, *, allow_unconfirmed=False):
        raise ExtractionAlreadyRunningError("already running")

    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_spec", fake_latest_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", fake_latest_preview)
    monkeypatch.setattr("app.api.v1.endpoints.projects.start_project_extraction", fake_start)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project, spec=spec))

    response = await async_client.post("/api/v1/projects/1/extract", json={"extract_anyway": True})
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "EXTRACTION_ALREADY_RUNNING"


# ─── Frontier preview endpoints ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_frontier_preview_returns_201(async_client, app, monkeypatch):
    """POST /frontier-preview creates and returns a preview with 201."""
    project = _project()
    preview = _frontier_preview()

    async def fake_create_fp(db, proj, *, max_urls=100):
        return preview

    async def fake_latest_fp(db, project_id):
        return None

    monkeypatch.setattr("app.api.v1.endpoints.projects.create_frontier_preview", fake_create_fp)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_frontier_preview", fake_latest_fp)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.post("/api/v1/projects/1/frontier-preview")

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 5
    assert body["project_id"] == 1
    assert body["scope_hash"] == "abc123"
    assert len(body["included_urls"]) == 1
    assert body["included_urls"][0]["reason_code"] == "SEED_URL"
    assert len(body["excluded_urls"]) == 1
    assert body["excluded_urls"][0]["reason_code"] == "CURRENT_PAGE_SCOPE"
    assert body["estimated_page_count"] == 1
    assert "quality_summary" in body


@pytest.mark.asyncio
async def test_create_frontier_preview_409_when_none_returned(async_client, app, monkeypatch):
    """POST /frontier-preview returns 409 when service returns None (no spec/seed)."""
    project = _project()

    async def fake_create_fp(db, proj, *, max_urls=100):
        return None

    monkeypatch.setattr("app.api.v1.endpoints.projects.create_frontier_preview", fake_create_fp)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.post("/api/v1/projects/1/frontier-preview")

    assert response.status_code == 409
    assert "no spec or seed URL" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_frontier_preview_409_for_wrong_state(async_client, app, monkeypatch):
    """POST /frontier-preview returns 409 when project state is QUEUED."""
    project = _project(state=ProjectState.QUEUED)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.post("/api/v1/projects/1/frontier-preview")

    assert response.status_code == 409
    assert "not ready" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_frontier_preview_returns_latest(async_client, app, monkeypatch):
    """GET /frontier-preview returns the latest stored preview."""
    preview = _frontier_preview()

    async def fake_latest_fp(db, project_id):
        return preview

    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_frontier_preview", fake_latest_fp)

    project = _project()
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/frontier-preview")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 5
    assert body["scope_hash"] == "abc123"


@pytest.mark.asyncio
async def test_get_frontier_preview_404_when_none(async_client, app, monkeypatch):
    """GET /frontier-preview returns 404 when no preview exists."""
    async def fake_latest_fp(db, project_id):
        return None

    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_frontier_preview", fake_latest_fp)

    project = _project()
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/frontier-preview")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_frontier_preview_404_for_other_user(async_client, app, monkeypatch):
    """GET /frontier-preview returns 404 when the project belongs to another user."""
    project = _project(user_id=2)

    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/frontier-preview")

    assert response.status_code == 404


# ─── Records-page paginated endpoint ─────────────────────────────────────────


def _record(idx: int) -> ExtractedRecord:
    return ExtractedRecord(
        id=idx,
        project_id=1,
        source_url=f"https://example.com/item/{idx}",
        raw_data={"Title": f"Item {idx}", "Price": f"${idx}.99"},
        normalized_data={"Title": f"Item {idx}", "Price": f"${idx}.99"},
        warnings=[],
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_records_page_returns_pagination_metadata(async_client, app, monkeypatch):
    """GET /records-page returns items, total, has_more, next_skip, and columns."""
    records = [_record(i) for i in range(1, 6)]

    async def fake_count(db, project_id):
        return 50

    async def fake_list(db, project_id, skip, limit):
        return records

    monkeypatch.setattr("app.api.v1.endpoints.projects.count_records", fake_count)
    monkeypatch.setattr("app.api.v1.endpoints.projects.list_records", fake_list)

    project = _project()
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/records-page?skip=0&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 50
    assert body["skip"] == 0
    assert body["limit"] == 5
    assert body["has_more"] is True
    assert body["next_skip"] == 5
    assert len(body["items"]) == 5
    assert set(body["columns"]) == {"Title", "Price"}


@pytest.mark.asyncio
async def test_records_page_last_page_has_no_next(async_client, app, monkeypatch):
    """GET /records-page on the last page returns has_more=False and next_skip=None."""
    records = [_record(i) for i in range(46, 51)]

    async def fake_count(db, project_id):
        return 50

    async def fake_list(db, project_id, skip, limit):
        return records

    monkeypatch.setattr("app.api.v1.endpoints.projects.count_records", fake_count)
    monkeypatch.setattr("app.api.v1.endpoints.projects.list_records", fake_list)

    project = _project()
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/records-page?skip=45&limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 50
    assert body["has_more"] is False
    assert body["next_skip"] is None


@pytest.mark.asyncio
async def test_records_page_empty_project(async_client, app, monkeypatch):
    """GET /records-page on a project with no records returns total=0."""
    async def fake_count(db, project_id):
        return 0

    async def fake_list(db, project_id, skip, limit):
        return []

    monkeypatch.setattr("app.api.v1.endpoints.projects.count_records", fake_count)
    monkeypatch.setattr("app.api.v1.endpoints.projects.list_records", fake_list)

    project = _project()
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/records-page")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["has_more"] is False
    assert body["items"] == []
    assert body["columns"] == []


@pytest.mark.asyncio
async def test_records_page_rejects_limit_over_500(async_client, app, monkeypatch):
    """GET /records-page with limit > 500 returns 422."""
    project = _project()
    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/records-page?limit=501")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_records_page_404_for_other_user(async_client, app):
    """GET /records-page returns 404 when the project belongs to another user."""
    project = _project(user_id=2)
    app.dependency_overrides[deps.get_current_user] = lambda: _user(user_id=1)
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1/records-page")

    assert response.status_code == 404


# ─── Quality summary and frontier preview in ProjectResponse ─────────────────


@pytest.mark.asyncio
async def test_project_response_includes_extraction_quality(async_client, app, monkeypatch):
    """GET /projects/{id} exposes extraction_quality when spec.quality_summary is set."""
    project = _project()
    quality_summary = {
        "overall": "needs_review",
        "field_success_rates": {"Title": 0.95, "Price": 0.42},
        "missing_field_rates": {"Price": 0.58},
        "warnings": [{"code": "FIELD_LOW_SUCCESS_RATE", "field": "Price", "success_rate": 0.42}],
    }
    spec = _spec(quality_summary=quality_summary)

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_latest_preview(db, project_id):
        return None

    async def fake_latest_fp(db, project_id):
        return None

    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", fake_latest_preview)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_frontier_preview", fake_latest_fp)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1")

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_quality"] is not None
    eq = body["extraction_quality"]
    assert eq["overall"] == "needs_review"
    assert eq["field_success_rates"]["Title"] == pytest.approx(0.95)
    assert eq["field_success_rates"]["Price"] == pytest.approx(0.42)
    assert len(eq["warnings"]) == 1


@pytest.mark.asyncio
async def test_project_response_includes_frontier_preview(async_client, app, monkeypatch):
    """GET /projects/{id} exposes frontier_preview when one exists."""
    project = _project()
    fp = _frontier_preview()

    async def fake_ensure_default_spec(db, proj):
        return _spec()

    async def fake_latest_preview(db, project_id):
        return None

    async def fake_latest_fp(db, project_id):
        return fp

    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", fake_latest_preview)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_frontier_preview", fake_latest_fp)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1")

    assert response.status_code == 200
    body = response.json()
    assert body["frontier_preview"] is not None
    fp_body = body["frontier_preview"]
    assert fp_body["id"] == 5
    assert fp_body["scope_hash"] == "abc123"
    assert len(fp_body["included_urls"]) == 1
    assert fp_body["included_urls"][0]["reason_code"] == "SEED_URL"


@pytest.mark.asyncio
async def test_project_response_null_quality_when_no_summary(async_client, app, monkeypatch):
    """GET /projects/{id} returns null extraction_quality when spec has no quality_summary."""
    project = _project()
    spec = _spec(quality_summary=None)

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_latest_preview(db, project_id):
        return None

    async def fake_latest_fp(db, project_id):
        return None

    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_preview", fake_latest_preview)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_frontier_preview", fake_latest_fp)

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield FakeSession(project=project))

    response = await async_client.get("/api/v1/projects/1")

    assert response.status_code == 200
    assert response.json()["extraction_quality"] is None


@pytest.mark.asyncio
async def test_spec_response_includes_crawl_scope(async_client, app, monkeypatch):
    """PATCH /spec response includes crawl_scope from the spec."""
    project = _project()
    scope = {
        "version": 1,
        "mode": "CURRENT_PAGE",
        "status": "USER_CONFIRMED",
        "seed_url": None,
        "max_pages": 500,
        "max_depth": None,
        "include_patterns": [],
        "exclude_patterns": [],
        "pagination": {},
        "link_rules": [],
        "ai_recommendation": None,
        "user_confirmed_at": None,
    }
    spec = _spec(crawl_scope=scope)

    async def fake_ensure_default_spec(db, proj):
        return spec

    async def fake_latest_spec(db, project_id):
        return spec

    monkeypatch.setattr("app.api.v1.endpoints.projects.ensure_default_spec", fake_ensure_default_spec)
    monkeypatch.setattr("app.api.v1.endpoints.projects.latest_spec", fake_latest_spec)

    db = FakeSession(project=project, spec=spec)
    db.refresh = AsyncMock()

    app.dependency_overrides[deps.get_current_user] = lambda: _user()
    app.dependency_overrides[deps.get_db] = lambda: (yield db)

    response = await async_client.patch("/api/v1/projects/1/spec", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["crawl_scope"] is not None
    assert body["crawl_scope"]["mode"] == "CURRENT_PAGE"
