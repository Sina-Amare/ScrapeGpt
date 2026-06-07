"""
Analysis job endpoints.

POST   /jobs           - Create analysis job (202)
GET    /jobs           - List user's jobs (paginated)
GET    /jobs/{id}      - Get job detail
POST   /jobs/{id}/cancel  - Cancel QUEUED/ANALYZING job
DELETE /jobs/{id}      - Delete terminal job
"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.job import ACTIVE_JOB_STATES, DELETABLE_JOB_STATES, Job, JobState
from app.models.user import User
from app.schemas.job import JobCreate, JobListItem, JobResponse
from app.services.job_admission import (
    JobAdmissionError,
    JobAdmissionErrorType,
    admit_job,
)
from app.services.job_executor import execute_job_pipeline
from app.services.job_state import transition_job_to_canceled

router = APIRouter(prefix="/jobs", tags=["Jobs"])


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        state=job.state.value,
        url=job.url,
        extraction_mode=job.extraction_mode.value,
        workflow_mode=job.workflow_mode.value,
        render_mode=job.render_mode.value,
        confidence=job.confidence,
        warnings=job.warnings or [],
        analysis=job.analysis,
        fetch_metadata=job.fetch_metadata,
        error=job.error,
        error_code=job.error_code,
        provider_config_id=job.provider_config_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _job_list_item(job: Job) -> JobListItem:
    return JobListItem(
        id=job.id,
        state=job.state.value,
        url=job.url,
        extraction_mode=job.extraction_mode.value,
        workflow_mode=job.workflow_mode.value,
        render_mode=job.render_mode.value,
        confidence=job.confidence,
        error=job.error,
        error_code=job.error_code,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an analysis job",
)
async def create_job(
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """
    Create a new analysis job and queue it for background processing.

    Returns 202 immediately with job details. Poll GET /jobs/{id} for status.

    Error codes in 409/400 responses:
    - NO_PROVIDER_CONFIGURED: user has no provider set up
    - ACTIVE_JOB_LIMIT_REACHED: too many concurrent jobs
    """
    url_str = str(payload.url)

    result = await admit_job(
        user=user,
        url=url_str,
        extraction_mode=payload.extraction_mode,
        workflow_mode=payload.workflow_mode,
        render_mode=payload.render_mode,
        provider_config_id=payload.provider_config_id,
        db=db,
    )

    if isinstance(result, JobAdmissionError):
        if result.error_type == JobAdmissionErrorType.NO_PROVIDER_CONFIGURED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": result.message,
                    "error_code": result.error_type.value,
                },
            )
        if result.error_type == JobAdmissionErrorType.ACTIVE_JOB_LIMIT_REACHED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": result.message,
                    "error_code": result.error_type.value,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message,
        )

    job = result.job
    provider_config = result.provider_config

    background_tasks.add_task(
        execute_job_pipeline,
        job_id=job.id,
        provider_config_id=provider_config.id,
    )

    return _job_response(job)


@router.get(
    "",
    response_model=list[JobListItem],
    summary="List analysis jobs",
)
async def list_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[JobListItem]:
    """List jobs for the authenticated user, newest first."""
    result = await db.execute(
        select(Job)
        .where(Job.user_id == user.id)
        .order_by(Job.created_at.desc())
        .limit(limit)
        .offset(skip)
    )
    jobs = result.scalars().all()
    return [_job_list_item(j) for j in jobs]


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get job detail",
)
async def get_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Get full job detail. Users can only access their own jobs."""
    job = await db.get(Job, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _job_response(job)


@router.post(
    "/{job_id}/cancel",
    response_model=JobResponse,
    summary="Cancel an active job",
)
async def cancel_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """
    Cancel a QUEUED or ANALYZING job.

    Returns 409 if the job is already terminal (not cancelable).
    Returns 404 if not found or not owned.
    """
    job = await db.get(Job, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job.state not in ACTIVE_JOB_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in state {job.state.value}. Only QUEUED and ANALYZING jobs can be canceled.",
        )

    result = await transition_job_to_canceled(
        job_id, expected_states=ACTIVE_JOB_STATES
    )
    if not result.success or not result.job:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.error or "Cancellation failed",
        )
    return _job_response(result.job)


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a terminal job",
)
async def delete_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a terminal job (AWAITING_SETUP, ANALYSIS_READY, FAILED, CANCELED).

    Returns 400 if the job is still active.
    Returns 404 if not found or not owned.
    """
    job = await db.get(Job, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job.state not in DELETABLE_JOB_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete job in state {job.state.value}. Wait for it to complete.",
        )

    await db.delete(job)
    await db.commit()
