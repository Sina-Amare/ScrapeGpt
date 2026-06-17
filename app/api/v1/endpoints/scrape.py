"""
Scraping endpoints with async pipeline.

SUPPORTED-LEGACY surface. This is the original one-shot scrape pipeline
(``ScrapeTask`` + ``admission.py`` + ``task_executor.py``). It is intentionally
kept and still maintained (SSRF-hardened), but it is NOT the primary product
flow — that is the project-based workflow under ``/projects`` (see
``endpoints/projects.py``). Prefer adding capabilities there. Removing this
surface is a product decision, not a drive-by cleanup.

POST /start          - Create task and queue for background processing
GET  /tasks          - List user tasks (paginated)
GET  /tasks/current  - Get user's current active task
GET  /tasks/{id}     - Get task status + content_length
"""

from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.api.deps import get_db, get_current_user
from app.core.rate_limit import limiter, SCRAPE_RATE_LIMIT
from app.models.scrape_task import ScrapeTask, TERMINAL_STATES
from app.models.user import User
from app.services.admission import (
    admit_scrape_task,
    AdmissionError,
    AdmissionErrorType,
)
from app.services.task_executor import execute_scrape_pipeline
from app.services.url_validator import URLValidationError, validate_url


router = APIRouter(prefix="/scrape", tags=["Scraping"])


class StartScrapeRequest(BaseModel):
    url: HttpUrl


class TaskResponse(BaseModel):
    """Task status response.

    content_length is only populated on single-task detail endpoints — the
    list endpoint defers the content column to avoid loading up to 50 KB per
    row across 100 rows.
    """

    task_id: int
    state: str
    url: str
    error: str | None = None
    result: dict | None = None
    message: str | None = None
    created_at: datetime | None = None
    content_length: int | None = None

    class Config:
        from_attributes = True


@router.post(
    "/start",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a scrape task",
    description="Create a new scrape task. Processing happens in background.",
    responses={429: {"description": "Rate limit exceeded"}},
)
@limiter.limit(SCRAPE_RATE_LIMIT)
async def start_scrape(
    request: Request,
    payload: StartScrapeRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """
    Start a new scrape task.

    1. Creates task in PERMISSION_GRANTED state
    2. Queues background processing
    3. Returns immediately with task_id

    User can poll GET /tasks/{id} for status.
    """
    url_str = str(payload.url)

    # SSRF-safe URL validation: block private/loopback/metadata IPs
    # before creating the task. This mirrors the project pipeline's
    # safety checks and gives immediate feedback to the user.
    try:
        validate_url(url_str)
    except URLValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc), "error_code": exc.reason.value},
        )

    result = await admit_scrape_task(user, url_str, db)

    if isinstance(result, AdmissionError):
        if result.error_type == AdmissionErrorType.TOO_MANY_ACTIVE_TASKS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": result.message,
                    "error_type": result.error_type.value,
                    "active_task_id": result.active_task_id,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message,
        )

    task = result.task

    background_tasks.add_task(
        execute_scrape_pipeline,
        task_id=task.id,
        user_id=user.id,
    )

    return TaskResponse(
        task_id=task.id,
        state=task.state.value,
        url=task.url,
        created_at=task.created_at,
        message="Task queued for processing",
    )


@router.get(
    "/tasks",
    response_model=list[TaskResponse],
    summary="List user tasks",
    description="Return the user's tasks, newest first. Supports skip/limit pagination.",
)
async def list_tasks(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    skip: int = Query(default=0, ge=0, description="Tasks to skip"),
    limit: int = Query(default=20, ge=1, le=100, description="Max tasks to return"),
) -> list[TaskResponse]:
    """List tasks for the authenticated user, newest first.

    The content column is deferred — content_length is not populated here.
    Use GET /tasks/{id} to get content_length for a specific task.
    """
    result = await db.execute(
        select(ScrapeTask)
        .options(defer(ScrapeTask.content))
        .where(ScrapeTask.user_id == user.id)
        .order_by(ScrapeTask.created_at.desc())
        .limit(limit)
        .offset(skip)
    )
    tasks = result.scalars().all()
    return [
        TaskResponse(
            task_id=t.id,
            state=t.state.value,
            url=t.url,
            error=t.error,
            result=t.result,
            created_at=t.created_at,
        )
        for t in tasks
    ]


@router.get(
    "/tasks/current",
    response_model=TaskResponse,
    summary="Get current active task",
    description="Get the user's current non-terminal task, if any.",
)
async def get_current_task(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Get user's current active task."""
    result = await db.execute(
        select(ScrapeTask)
        .where(
            ScrapeTask.user_id == user.id,
            ScrapeTask.state.notin_(TERMINAL_STATES),
        )
        .order_by(ScrapeTask.created_at.desc())
        .limit(1)
    )
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active task",
        )

    return TaskResponse(
        task_id=task.id,
        state=task.state.value,
        url=task.url,
        error=task.error,
        result=task.result,
        created_at=task.created_at,
        content_length=len(task.content) if task.content else None,
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    summary="Get task status",
    description="Get the status of a scrape task by ID.",
)
async def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Get task status by ID. Users can only see their own tasks."""
    task = await db.get(ScrapeTask, task_id)

    if not task or task.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    return TaskResponse(
        task_id=task.id,
        state=task.state.value,
        url=task.url,
        error=task.error,
        result=task.result,
        created_at=task.created_at,
        content_length=len(task.content) if task.content else None,
    )


@router.delete(
    "/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete scrape task",
    description="Delete a completed or failed scrape task from the database.",
)
async def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a task owned by the user if it is in a terminal state."""
    task = await db.get(ScrapeTask, task_id)

    if not task or task.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    if not task.is_terminal:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete an active task. Wait for it to complete or fail.",
        )

    await db.delete(task)
    await db.commit()

