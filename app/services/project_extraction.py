"""Initial extraction executor for project specs.

This phase persists the extraction/result contract using the seed-page preview.
The crawl_pages and lease columns are in place for the later multi-page crawler.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import (
    CrawlPage,
    CrawlPageState,
    Export,
    ExtractedRecord,
    ExtractionSpec,
    PreviewResult,
    Project,
    ProjectState,
)
from app.services.project_preview import build_preview_payload


def _spec_hash(spec: ExtractionSpec) -> str:
    payload = {
        "fields": spec.fields or [],
        "content_config": spec.content_config or {},
        "url_patterns": spec.url_patterns or [],
        "page_limit": spec.page_limit,
        "export_format": spec.export_format,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


async def run_seed_extraction(
    db: AsyncSession,
    project: Project,
    spec: ExtractionSpec,
    preview: PreviewResult | None,
) -> dict[str, Any]:
    project.state = ProjectState.DISCOVERING
    await db.flush()

    await db.execute(delete(ExtractedRecord).where(ExtractedRecord.project_id == project.id))
    await db.execute(delete(CrawlPage).where(CrawlPage.project_id == project.id))
    await db.execute(delete(Export).where(Export.project_id == project.id))

    page = CrawlPage(
        project_id=project.id,
        url=project.url,
        normalized_url=project.normalized_url or project.url,
        state=CrawlPageState.FETCHED,
        depth=0,
    )
    db.add(page)
    await db.flush()

    project.state = ProjectState.EXTRACTING
    payload = (
        {
            "sample_records": preview.sample_records,
            "warnings": preview.warnings,
            "missing_fields": preview.missing_fields,
            "quality_summary": preview.quality_summary,
        }
        if preview is not None
        else build_preview_payload(project, spec)
    )

    records = payload.get("sample_records") or []
    for record in records:
        db.add(
            ExtractedRecord(
                project_id=project.id,
                page_id=page.id,
                source_url=project.url,
                raw_data=record,
                normalized_data=record,
                warnings=payload.get("warnings") or [],
            )
        )
    page.state = CrawlPageState.EXTRACTED

    project.state = ProjectState.EXPORTING
    export = Export(
        project_id=project.id,
        format=spec.export_format or "csv",
        record_count=len(records),
        spec_hash=_spec_hash(spec),
    )
    db.add(export)
    project.state = ProjectState.COMPLETED
    await db.flush()

    return {
        "record_count": len(records),
        "export_id": export.id,
    }


async def list_records(
    db: AsyncSession,
    project_id: int,
    skip: int,
    limit: int,
) -> list[ExtractedRecord]:
    result = await db.execute(
        select(ExtractedRecord)
        .where(ExtractedRecord.project_id == project_id)
        .order_by(ExtractedRecord.id.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())
