"""Project workflow endpoints."""

import csv
import io

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
    ExtractionSpec,
    Project,
    ProjectState,
)
from app.models.user import User
from app.schemas.project import (
    ExtractRequest,
    ExtractionProgress,
    ExtractionSpecResponse,
    ExtractionSpecUpdate,
    PreviewResponse,
    ProjectAnalyzeRequest,
    ProjectListItem,
    ProjectResponse,
    RecordResponse,
)
from app.services.extraction_spec_service import ensure_default_spec, latest_spec, selected_field_count
from app.services.job_admission import JobAdmissionError, JobAdmissionErrorType, admit_job
from app.services.job_executor import execute_job_pipeline
from app.services.job_state import transition_job_to_canceled
from app.services.project_extraction import list_records, run_seed_extraction
from app.services.project_preview import create_preview, latest_preview
from app.services.project_status import confidence_label, detected_type, product_status_for

router = APIRouter(prefix="/projects", tags=["Projects"])


async def _progress(db: AsyncSession, project_id: int) -> ExtractionProgress:
    crawl_pages = await db.scalar(
        select(func.count(CrawlPage.id)).where(CrawlPage.project_id == project_id)
    )
    records = await db.scalar(
        select(func.count(ExtractedRecord.id)).where(ExtractedRecord.project_id == project_id)
    )
    exports = await db.scalar(
        select(func.count(Export.id)).where(Export.project_id == project_id)
    )
    return ExtractionProgress(
        crawl_pages_total=int(crawl_pages or 0),
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
        created_at=spec.created_at,
        updated_at=spec.updated_at,
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


async def _project_response(db: AsyncSession, project: Project) -> ProjectResponse:
    spec = await ensure_default_spec(db, project)
    preview = await latest_preview(db, project.id)
    status_info = product_status_for(project)
    last_activity = project.updated_at or project.created_at
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
    items: list[ProjectListItem] = []
    for project in projects:
        spec = await ensure_default_spec(db, project)
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
    await db.commit()
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

    preview = await create_preview(db, project, spec)
    await db.commit()
    await db.refresh(preview)
    return _preview_response(preview)  # type: ignore[return-value]


@router.post("/{project_id}/extract", response_model=ProjectResponse, summary="Extract records")
async def extract_project(
    project_id: int,
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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Preview before extracting, or choose extract anyway")
    if project.state not in {
        ProjectState.AWAITING_SETUP,
        ProjectState.ANALYSIS_READY,
        ProjectState.PREVIEW_READY,
        ProjectState.COMPLETED,
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project is not ready for extraction")

    await run_seed_extraction(db, project, spec, preview)
    await db.commit()
    await db.refresh(project)
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


@router.get("/{project_id}/export", summary="Export generated results")
async def export_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    format: str = Query(default="csv", pattern="^(csv|json)$"),
) -> Response:
    await _owned_project(db, user, project_id)
    records = await list_records(db, project_id, 0, 5000)
    data = [record.normalized_data or record.raw_data for record in records]
    if format == "json":
        import json

        return Response(
            content=json.dumps(data),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="project-{project_id}.json"'},
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete an active project")
    await db.delete(project)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
