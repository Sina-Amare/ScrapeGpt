"""Service for retrying a FAILED project in-place."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Project, ProjectState
from app.models.provider_config import ProviderConfig
from app.models.user import User
from app.services.extraction_spec_service import latest_spec
from app.services.job_admission import resolve_provider_for_user
from app.services.project_preview import latest_preview, preview_matches_spec

logger = logging.getLogger(__name__)


class RetryError(Exception):
    """Raised when a retry cannot proceed."""

    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


async def retry_failed_project(
    db: AsyncSession,
    project: Project,
    user: User,
    provider_config_id: int | None = None,
) -> tuple[Project, ProviderConfig | None]:
    """Reset a FAILED project for retry.

    Returns (project, provider_config):
    - provider_config is set when the caller must re-queue an analysis job.
    - provider_config is None when the project was reset to a ready state (no
      new background job needed; the user re-triggers extraction manually).

    ``provider_config_id`` lets the user retry a failed *analysis* with a
    different provider/model. It is ignored when analysis already succeeded
    (no re-analysis happens).

    Raises RetryError if the project cannot be retried.
    """
    if project.state != ProjectState.FAILED:
        raise RetryError(
            "Only FAILED projects can be retried.",
            "NOT_FAILED",
        )

    old_error_code = project.error_code
    project.error = None
    project.error_code = None

    if project.analysis:
        # Analysis succeeded before the failure. Reset to the highest valid
        # completed stage so the user can re-trigger extraction without paying
        # for LLM re-analysis.
        spec = await latest_spec(db, project.id)
        preview = await latest_preview(db, project.id)

        # Only return to PREVIEW_READY if the saved preview validates the exact
        # current spec shape. A content fingerprint catches legacy/direct JSON
        # spec edits that do not reliably move updated_at.
        if preview_matches_spec(preview, spec):
            project.transition_to(ProjectState.PREVIEW_READY)
        else:
            project.transition_to(ProjectState.ANALYSIS_READY)

        logger.info(
            "project.retried",
            extra={
                "project_id": project.id,
                "old_error_code": old_error_code,
                "new_state": project.state.value,
            },
        )
        return project, None

    # Analysis itself failed. Resolve a provider and re-queue. An explicit
    # override lets the user switch provider/model when the AI call failed.
    requested = provider_config_id if provider_config_id is not None else project.provider_config_id
    provider = await resolve_provider_for_user(db, user, requested)
    if provider is None:
        raise RetryError(
            "No provider configured. Add a provider in Settings → Providers before retrying.",
            "NO_PROVIDER_CONFIGURED",
        )

    # Persist the chosen provider so the re-queued analysis uses it.
    project.provider_config_id = provider.id
    project.transition_to(ProjectState.QUEUED)

    logger.info(
        "project.retried",
        extra={
            "project_id": project.id,
            "old_error_code": old_error_code,
            "new_state": project.state.value,
            "provider_config_id": provider.id,
        },
    )
    return project, provider
