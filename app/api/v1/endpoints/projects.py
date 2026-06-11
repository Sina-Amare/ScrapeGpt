"""Project workflow endpoints."""

import csv
import io
import json
import logging
import time
import zipfile
from html import escape
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.rate_limit import SCRAPE_RATE_LIMIT, limiter
from app.models.job import (
    ACTIVE_PROJECT_STATES,
    DELETABLE_PROJECT_STATES,
    CrawlPage,
    Export,
    ExtractedRecord,
    ExtractionMode,
    ExtractionSpec,
    Project,
    ProjectState,
)
from app.models.user import User
from app.schemas.project import (
    ExtractRequest,
    ExtractionProgress,
    ExtractionQuality,
    ExtractionSpecResponse,
    ExtractionSpecUpdate,
    FrontierPreviewResponse,
    FrontierUrlDecision,
    PreviewResponse,
    ProjectAnalyzeRequest,
    ProjectListItem,
    ProjectResponse,
    RecordPageResponse,
    RecordResponse,
)
from app.services.crawl_scope import ScopeConfirmationError
from app.services.extraction_spec_service import ensure_default_spec, latest_spec, selected_field_count
from app.services.frontierpreview import create_frontier_preview, latest_frontier_preview
from app.services.job_admission import JobAdmissionError, JobAdmissionErrorType, admit_job
from app.services.job_executor import execute_job_pipeline
from app.services.job_state import transition_job_to_canceled
from app.services.project_lifecycle import delete_project_tree
from app.services.project_extraction import count_records, execute_project_extraction, list_records, start_project_extraction
from app.services.project_preview import create_preview, latest_preview
from app.services.project_status import confidence_label, detected_type, product_status_for

router = APIRouter(prefix="/projects", tags=["Projects"])

logger = logging.getLogger(__name__)


async def _progress(db: AsyncSession, project_id: int) -> ExtractionProgress:
    crawl_pages_total = await db.scalar(
        select(func.count(CrawlPage.id)).where(CrawlPage.project_id == project_id)
    )
    page_counts_result = await db.execute(
        select(CrawlPage.state, func.count(CrawlPage.id))
        .where(CrawlPage.project_id == project_id)
        .group_by(CrawlPage.state)
    )
    page_counts = {state.value if hasattr(state, "value") else str(state): int(count) for state, count in page_counts_result}
    records = await db.scalar(
        select(func.count(ExtractedRecord.id)).where(ExtractedRecord.project_id == project_id)
    )
    exports = await db.scalar(
        select(func.count(Export.id)).where(Export.project_id == project_id)
    )
    return ExtractionProgress(
        crawl_pages_total=int(crawl_pages_total or 0),
        crawl_pages_pending=page_counts.get("PENDING", 0),
        crawl_pages_fetching=page_counts.get("FETCHING", 0),
        crawl_pages_extracted=page_counts.get("EXTRACTED", 0),
        crawl_pages_blocked=page_counts.get("BLOCKED", 0),
        crawl_pages_failed=page_counts.get("FAILED", 0),
        extracted_records_total=int(records or 0),
        exports_total=int(exports or 0),
    )


def _spec_response(spec: ExtractionSpec | None) -> ExtractionSpecResponse | None:
    if spec is None:
        return None
    return ExtractionSpecResponse(
        id=spec.id,
        project_id=spec.project_id,
        mode=spec.mode.value,
        fields=spec.fields or [],
        content_config=spec.content_config or {},
        url_patterns=spec.url_patterns or [],
        page_limit=spec.page_limit,
        export_format=spec.export_format,
        crawl_scope=spec.crawl_scope,
        quality_summary=spec.quality_summary,
        created_at=spec.created_at,
        updated_at=spec.updated_at,
    )


def _frontier_preview_response(preview) -> FrontierPreviewResponse | None:
    if preview is None:
        return None
    return FrontierPreviewResponse(
        id=preview.id,
        project_id=preview.project_id,
        spec_id=preview.spec_id,
        scope_hash=preview.scope_hash,
        included_urls=[FrontierUrlDecision(**d) for d in (preview.included_urls or [])],
        excluded_urls=[FrontierUrlDecision(**d) for d in (preview.excluded_urls or [])],
        estimated_page_count=preview.estimated_page_count,
        warnings=preview.warnings or [],
        quality_summary=preview.quality_summary or {},
        created_at=preview.created_at,
    )


def _preview_response(preview) -> PreviewResponse | None:
    if preview is None:
        return None
    return PreviewResponse(
        id=preview.id,
        project_id=preview.project_id,
        spec_id=preview.spec_id,
        sample_records=preview.sample_records or [],
        warnings=preview.warnings or [],
        missing_fields=preview.missing_fields or [],
        quality_summary=preview.quality_summary or {},
        created_at=preview.created_at,
    )


def _extraction_quality(spec: ExtractionSpec | None) -> ExtractionQuality | None:
    if spec is None or not spec.quality_summary:
        return None
    qs = spec.quality_summary
    return ExtractionQuality(
        overall=qs.get("overall", "unknown"),
        field_success_rates=qs.get("field_success_rates", {}),
        missing_field_rates=qs.get("missing_field_rates", {}),
        warnings=qs.get("warnings", []),
    )


async def _project_response(db: AsyncSession, project: Project) -> ProjectResponse:
    spec = await ensure_default_spec(db, project)
    preview = await latest_preview(db, project.id)
    frontier_preview = await latest_frontier_preview(db, project.id)
    status_info = product_status_for(project)
    last_activity = project.updated_at or project.created_at
    # Compute preview_stale: spec was updated after the last preview ran.
    preview_stale = False
    if spec is not None and preview is not None:
        spec_updated = spec.updated_at or spec.created_at
        preview_created = preview.created_at
        if spec_updated is not None and preview_created is not None:
            preview_stale = spec_updated > preview_created
    return ProjectResponse(
        id=project.id,
        url=project.url,
        system_state=project.state.value,
        product_status=status_info.code,
        product_status_label=status_info.label,
        product_status_tone=status_info.tone,
        detected_type=detected_type(project),
        confidence=project.confidence,
        confidence_label=confidence_label(project.confidence, project.warnings or []),
        selected_field_count=selected_field_count(spec),
        extraction_mode=project.extraction_mode.value,
        workflow_mode=project.workflow_mode.value,
        render_mode=project.render_mode.value,
        provider_config_id=project.provider_config_id,
        warnings=project.warnings or [],
        analysis=project.analysis,
        fetch_metadata=project.fetch_metadata,
        spec=_spec_response(spec),
        preview=_preview_response(preview),
        frontier_preview=_frontier_preview_response(frontier_preview),
        extraction_quality=_extraction_quality(spec),
        preview_stale=preview_stale,
        progress=await _progress(db, project.id),
        last_activity=last_activity,
        error=project.error,
        error_code=project.error_code,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def _owned_project(db: AsyncSession, user: User, project_id: int) -> Project:
    project = await db.get(Project, project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.post(
    "/analyze",
    response_model=ProjectResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Analyze a URL and create a project",
)
@limiter.limit(SCRAPE_RATE_LIMIT)
async def analyze_project(
    request: Request,
    payload: ProjectAnalyzeRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    advanced = payload.advanced
    result = await admit_job(
        user=user,
        url=str(payload.url),
        extraction_mode=advanced.extraction_mode if advanced and advanced.extraction_mode else "STRUCTURED",
        workflow_mode=advanced.workflow_mode if advanced and advanced.workflow_mode else "GUIDED",
        render_mode=advanced.render_mode if advanced and advanced.render_mode else "AUTO",
        provider_config_id=advanced.provider_config_id if advanced else None,
        db=db,
    )

    if isinstance(result, JobAdmissionError):
        if result.error_type in {
            JobAdmissionErrorType.NO_PROVIDER_CONFIGURED,
            JobAdmissionErrorType.ACTIVE_JOB_LIMIT_REACHED,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": result.message, "error_code": result.error_type.value},
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.message)

    background_tasks.add_task(
        execute_job_pipeline,
        job_id=result.job.id,
        provider_config_id=result.provider_config.id,
    )
    return await _project_response(db, result.job)


@router.get("", response_model=list[ProjectListItem], summary="List projects")
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ProjectListItem]:
    result = await db.execute(
        select(Project)
        .where(Project.user_id == user.id)
        .order_by(Project.created_at.desc())
        .limit(limit)
        .offset(skip)
    )
    projects = result.scalars().all()

    # Batch-load the latest spec for each project in a single query.
    # The list view only needs selected_field_count, so we never auto-create
    # a spec here — that happens lazily when the user opens the workspace.
    specs_by_project: dict[int, ExtractionSpec | None] = {p.id: None for p in projects}
    if projects:
        spec_rows = await db.execute(
            select(ExtractionSpec)
            .where(ExtractionSpec.project_id.in_([p.id for p in projects]))
            .order_by(ExtractionSpec.created_at.desc(), ExtractionSpec.id.desc())
        )
        for spec in spec_rows.scalars().all():
            if specs_by_project.get(spec.project_id) is None:
                specs_by_project[spec.project_id] = spec

    items: list[ProjectListItem] = []
    for project in projects:
        spec = specs_by_project.get(project.id)
        status_info = product_status_for(project)
        items.append(
            ProjectListItem(
                id=project.id,
                url=project.url,
                system_state=project.state.value,
                product_status=status_info.code,
                product_status_label=status_info.label,
                product_status_tone=status_info.tone,
                detected_type=detected_type(project),
                confidence=project.confidence,
                confidence_label=confidence_label(project.confidence, project.warnings or []),
                selected_field_count=selected_field_count(spec),
                extraction_mode=project.extraction_mode.value,
                last_activity=project.updated_at or project.created_at,
                error=project.error,
                error_code=project.error_code,
            )
        )
    return items


@router.get("/{project_id}", response_model=ProjectResponse, summary="Get project detail")
async def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project = await _owned_project(db, user, project_id)
    response = await _project_response(db, project)
    await db.commit()
    return response


@router.patch("/{project_id}/spec", response_model=ExtractionSpecResponse, summary="Update extraction spec")
async def update_project_spec(
    project_id: int,
    payload: ExtractionSpecUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExtractionSpecResponse:
    project = await _owned_project(db, user, project_id)
    spec = await ensure_default_spec(db, project)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project analysis is not ready")

    if payload.fields is not None:
        spec.fields = [field.model_dump() for field in payload.fields]
    if payload.content_config is not None:
        spec.content_config = payload.content_config
    if payload.url_patterns is not None:
        spec.url_patterns = payload.url_patterns
    if payload.page_limit is not None:
        spec.page_limit = payload.page_limit
    if payload.export_format is not None:
        spec.export_format = payload.export_format
    if payload.crawl_scope is not None:
        spec.crawl_scope = payload.crawl_scope.model_dump(mode="json")

    await db.commit()
    await db.refresh(spec)
    return _spec_response(spec)  # type: ignore[return-value]


@router.post("/{project_id}/preview", response_model=PreviewResponse, summary="Preview selected fields")
async def preview_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    project = await _owned_project(db, user, project_id)
    spec = await latest_spec(db, project.id)
    if spec is None:
        spec = await ensure_default_spec(db, project)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Select fields before previewing")
    if project.state not in {
        ProjectState.AWAITING_SETUP,
        ProjectState.ANALYSIS_READY,
        ProjectState.PREVIEW_READY,
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project is not ready for preview")

    try:
        preview = await create_preview(db, project, spec)
    except Exception as exc:
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(preview)
    return _preview_response(preview)  # type: ignore[return-value]


@router.post(
    "/{project_id}/frontier-preview",
    response_model=FrontierPreviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate crawl frontier preview",
)
async def create_frontier_preview_endpoint(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FrontierPreviewResponse:
    project = await _owned_project(db, user, project_id)
    if project.state not in {
        ProjectState.AWAITING_SETUP,
        ProjectState.ANALYSIS_READY,
        ProjectState.PREVIEW_READY,
        ProjectState.COMPLETED,
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project is not ready for frontier preview")
    try:
        preview = await create_frontier_preview(db, project)
    except Exception as exc:
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not generate frontier preview: no spec or seed URL available",
        )
    await db.commit()
    await db.refresh(preview)
    return _frontier_preview_response(preview)  # type: ignore[return-value]


@router.get(
    "/{project_id}/frontier-preview",
    response_model=FrontierPreviewResponse,
    summary="Get latest crawl frontier preview",
)
async def get_frontier_preview(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FrontierPreviewResponse:
    await _owned_project(db, user, project_id)
    preview = await latest_frontier_preview(db, project_id)
    if preview is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No frontier preview found")
    return _frontier_preview_response(preview)  # type: ignore[return-value]


@router.post("/{project_id}/extract", response_model=ProjectResponse, summary="Extract records")
async def extract_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    payload: ExtractRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project = await _owned_project(db, user, project_id)
    spec = await latest_spec(db, project.id)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Select fields before extracting")
    preview = await latest_preview(db, project.id)
    extract_anyway = bool(payload.extract_anyway) if payload else False
    if preview is None and not extract_anyway:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Preview before extracting, or choose extract anyway",
                "error_code": "NO_PREVIEW",
            },
        )
    # Soft gate: warn if preview is stale (spec changed after last preview).
    if preview is not None and not extract_anyway:
        spec_updated = spec.updated_at or spec.created_at
        preview_created = preview.created_at
        if spec_updated is not None and preview_created is not None and spec_updated > preview_created:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        "Extraction spec was updated after the last preview. "
                        "Run preview again to verify selectors, or choose "
                        "extract anyway."
                    ),
                    "error_code": "STALE_PREVIEW",
                },
            )
    # Gate: structured-mode preview with zero records means selectors matched nothing.
    if (
        preview is not None
        and not extract_anyway
        and spec.mode == ExtractionMode.STRUCTURED
        and len(preview.sample_records or []) == 0
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "Preview found no records — CSS selectors likely do not "
                    "match the current page. Run preview again to check for "
                    "selector errors, or choose extract anyway."
                ),
                "error_code": "ZERO_PREVIEW_RECORDS",
            },
        )
    if project.state not in {
        ProjectState.AWAITING_SETUP,
        ProjectState.ANALYSIS_READY,
        ProjectState.PREVIEW_READY,
        ProjectState.COMPLETED,
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project is not ready for extraction")

    try:
        await start_project_extraction(db, project, spec)
    except ScopeConfirmationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "error_code": exc.code, "scope": exc.scope},
        )
    await db.commit()
    await db.refresh(project)
    background_tasks.add_task(execute_project_extraction, project.id, spec.id)
    return await _project_response(db, project)


@router.get("/{project_id}/records", response_model=list[RecordResponse], summary="List extracted records")
async def get_project_records(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[RecordResponse]:
    await _owned_project(db, user, project_id)
    records = await list_records(db, project_id, skip, limit)
    return [
        RecordResponse(
            id=record.id,
            source_url=record.source_url,
            raw_data=record.raw_data,
            normalized_data=record.normalized_data,
            warnings=record.warnings or [],
            created_at=record.created_at,
        )
        for record in records
    ]


@router.get(
    "/{project_id}/records-page",
    response_model=RecordPageResponse,
    summary="Paginated extracted records",
)
async def get_project_records_page(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> RecordPageResponse:
    await _owned_project(db, user, project_id)
    total = await count_records(db, project_id)
    records = await list_records(db, project_id, skip, limit)
    items = [
        RecordResponse(
            id=record.id,
            source_url=record.source_url,
            raw_data=record.raw_data,
            normalized_data=record.normalized_data,
            warnings=record.warnings or [],
            created_at=record.created_at,
        )
        for record in records
    ]
    columns: list[str] = sorted({
        key
        for record in records
        for key in (record.normalized_data or record.raw_data or {}).keys()
    })
    has_more = (skip + limit) < total
    next_skip = skip + limit if has_more else None
    return RecordPageResponse(
        items=items,
        total=total,
        skip=skip,
        limit=limit,
        next_skip=next_skip,
        has_more=has_more,
        columns=columns,
    )


@router.get("/{project_id}/export", summary="Export generated results")
async def export_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    format: str = Query(default="csv", pattern="^(csv|json|xlsx)$"),
) -> Response:
    logger.info(
        "export.started",
        extra={"project_id": project_id, "user_id": user.id, "format": format},
    )
    start_time = time.monotonic()
    try:
        await _owned_project(db, user, project_id)
        total = await count_records(db, project_id)
        if total > 10_000:
            logger.warning(
                "export.large_export",
                extra={
                    "project_id": project_id,
                    "total_records": total,
                    "format": format,
                },
            )
        # Fetch all records in chunks to avoid silent truncation.
        # Previous implementation had a hard cap of 5000 that silently
        # dropped records beyond that limit.
        chunk_size = 1000
        data: list[dict[str, Any]] = []
        for skip in range(0, total, chunk_size):
            records = await list_records(db, project_id, skip, chunk_size)
            data.extend(record.normalized_data or record.raw_data for record in records)
        duration_ms = round((time.monotonic() - start_time) * 1000, 1)
        logger.info(
            "export.completed",
            extra={
                "project_id": project_id,
                "format": format,
                "record_count": len(data),
                "total_records": total,
                "duration_ms": duration_ms,
            },
        )
        if format == "json":
            return Response(
                content=json.dumps(data),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="project-{project_id}.json"'},
            )
        if format == "xlsx":
            return Response(
                content=_xlsx_bytes(data),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="project-{project_id}.xlsx"'},
            )

        output = io.StringIO()
        fieldnames = sorted({key for row in data for key in row.keys()})
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="project-{project_id}.csv"'},
        )
    except Exception as exc:
        logger.error(
            "export.failed",
            extra={
                "project_id": project_id,
                "format": format,
                "error_type": type(exc).__name__,
            },
        )
        raise


def _xlsx_bytes(rows: list[dict]) -> bytes:
    """Generate a small XLSX workbook using the stdlib zipfile module."""
    columns = sorted({key for row in rows for key in row.keys()})
    sheet_rows = [_xlsx_row(1, columns)]
    for index, row in enumerate(rows, start=2):
        sheet_rows.append(_xlsx_row(index, [row.get(column, "") for column in columns]))

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Results" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def _xlsx_row(index: int, values: list) -> str:
    cells = []
    for col_index, value in enumerate(values, start=1):
        ref = f"{_excel_column(col_index)}{index}"
        text = escape("" if value is None else str(value))
        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
    return f'<row r="{index}">' + "".join(cells) + "</row>"


def _excel_column(index: int) -> str:
    label = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


@router.post("/{project_id}/cancel", response_model=ProjectResponse, summary="Cancel active project")
async def cancel_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project = await _owned_project(db, user, project_id)
    if project.state not in ACTIVE_PROJECT_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project is not active")
    result = await transition_job_to_canceled(project_id, expected_states=ACTIVE_PROJECT_STATES)
    if not result.success or not result.job:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.error or "Cancellation failed")
    return await _project_response(db, result.job)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    summary="Delete project",
)
async def delete_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await _owned_project(db, user, project_id)
    if project.state not in DELETABLE_PROJECT_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete an active project",
        )
    try:
        await delete_project_tree(db, project)
    except Exception:
        logger.exception(
            "project.delete_failed",
            extra={"project_id": project_id, "user_id": user.id},
        )
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete project. A background task may hold a row "
                "lock; wait 30 seconds and try again."
            ),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
