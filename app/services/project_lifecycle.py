"""Project lifecycle operations that span project-owned tables."""

from __future__ import annotations

import logging

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import (
    CrawlPage,
    Export,
    ExtractedRecord,
    ExtractionRun,
    ExtractionSpec,
    FrontierPreview,
    PreviewResult,
    Project,
)

logger = logging.getLogger(__name__)


async def delete_project_tree(db: AsyncSession, project: Project) -> None:
    """Delete a project and all project-owned artifacts.

    This intentionally uses bulk deletes in dependency order instead of
    relying solely on ORM relationship cascades. Projects can accumulate
    specs, previews, frontier previews, crawl pages, exports, and records
    across failed preview/extraction attempts; deleting each table
    explicitly keeps the endpoint deterministic for those partial states.
    """
    project_id = project.id

    # Order matters: delete a table before the tables it references. The
    # run-scoped children (records, exports, pages) reference extraction_runs,
    # so ExtractionRun is deleted after them and before the project. It is
    # listed explicitly rather than left to the projects.id ON DELETE CASCADE,
    # both to keep this function's audit log complete and so correctness does
    # not silently depend on that single FK staying CASCADE.
    for model in (
        ExtractedRecord,
        Export,
        FrontierPreview,
        PreviewResult,
        CrawlPage,
        ExtractionRun,
        ExtractionSpec,
    ):
        result = await db.execute(delete(model).where(model.project_id == project_id))
        logger.debug(
            "project.delete_artifacts",
            extra={
                "project_id": project_id,
                "table": model.__tablename__,
                "rowcount": result.rowcount,
            },
        )

    result = await db.execute(delete(Project).where(Project.id == project_id))
    logger.info(
        "project.deleted",
        extra={
            "project_id": project_id,
            "rowcount": result.rowcount,
        },
    )
    await db.commit()
