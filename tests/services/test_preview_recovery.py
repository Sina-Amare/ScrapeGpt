"""Tier 1 #2: a transient preview-fetch failure (browser-driver crash, network,
anti-bot) reverts the project to the ready state it came from instead of
stranding it in FAILED. A genuine spec/config problem (InteractionError) still
hard-fails the project with its precise code.
"""

from __future__ import annotations

import pytest

from app.models.job import ExtractionSpec, Project, ProjectState
from app.services import project_preview
from app.services.fetcher import FetchError
from app.services.interaction_profile import InteractionError


class _FakeDB:
    """Minimal async session: create_preview's failure paths only flush."""

    async def flush(self):
        return None

    async def refresh(self, obj):
        return None

    def add(self, obj):
        return None


def _project(state: ProjectState) -> Project:
    p = Project(id=1, user_id=1, url="https://example.com")
    p.state = state
    # A stale error from a previous attempt must be cleared/overwritten.
    p.error = "stale"
    p.error_code = "STALE"
    return p


def _spec() -> ExtractionSpec:
    return ExtractionSpec(id=1, project_id=1, fields=[])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior",
    [
        ProjectState.ANALYSIS_READY,
        ProjectState.PREVIEW_READY,
        ProjectState.AWAITING_SETUP,
    ],
)
async def test_transient_browser_crash_reverts_to_prior_state(monkeypatch, prior):
    project = _project(prior)

    async def boom(*_a, **_k):
        err = RuntimeError("The browser closed unexpectedly while loading.")
        # build_selector_preview_payload preserves the fetch code this way.
        err.error_code = "BROWSER_DRIVER_CRASHED"  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(project_preview, "build_selector_preview_payload", boom)

    with pytest.raises(RuntimeError):
        await project_preview.create_preview(_FakeDB(), project, _spec())

    assert project.state == prior  # reverted, NOT FAILED
    assert project.error_code == "BROWSER_DRIVER_CRASHED"
    assert "Preview failed" in (project.error or "")


@pytest.mark.asyncio
async def test_transient_fetch_error_reverts_and_keeps_code(monkeypatch):
    project = _project(ProjectState.ANALYSIS_READY)

    async def boom(*_a, **_k):
        raise FetchError("network down", "FETCH_FAILED")

    monkeypatch.setattr(project_preview, "build_selector_preview_payload", boom)

    with pytest.raises(FetchError):
        await project_preview.create_preview(_FakeDB(), project, _spec())

    assert project.state == ProjectState.ANALYSIS_READY
    assert project.error_code == "FETCH_FAILED"


@pytest.mark.asyncio
async def test_interaction_error_hard_fails_with_precise_code(monkeypatch):
    project = _project(ProjectState.ANALYSIS_READY)

    # A genuine spec/config problem (too many variant combinations) is the
    # remaining hard-fail path — a missing/crashing browser now degrades
    # gracefully inside extraction rather than raising InteractionError.
    async def boom(*_a, **_k):
        raise InteractionError(
            "Selected variants produce too many combinations.",
            code="INTERACTION_VARIANT_LIMIT_EXCEEDED",
        )

    monkeypatch.setattr(project_preview, "build_selector_preview_payload", boom)

    with pytest.raises(InteractionError):
        await project_preview.create_preview(_FakeDB(), project, _spec())

    assert project.state == ProjectState.FAILED  # genuine spec problem
    assert project.error_code == "INTERACTION_VARIANT_LIMIT_EXCEEDED"


def test_previewing_revert_transitions_are_legal():
    """The state machine itself must permit PREVIEWING -> each ready state so the
    revert goes through transition_to() (no direct state assignment)."""
    for target in (
        ProjectState.AWAITING_SETUP,
        ProjectState.ANALYSIS_READY,
        ProjectState.PREVIEW_READY,
    ):
        p = Project(id=1, user_id=1, url="https://example.com")
        p.state = ProjectState.PREVIEWING
        p.transition_to(target)  # must not raise
        assert p.state == target
