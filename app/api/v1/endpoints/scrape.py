"""
Scraping endpoints with async pipeline.

POST /start - Create task and queue for background processing
GET /tasks/{id} - Get task status
GET /tasks/current - Get user's current active task
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.scrape_task import ScrapeTask, TaskState, TERMINAL_STATES
from app.models.user import User
from app.services.admission import (
    admit_scrape_task,
    AdmissionError,
    AdmissionErrorType,
)
from app.services.task_executor import execute_scrape_pipeline


router = APIRouter(prefix="/scrape", tags=["Scraping"])


# Request/Response schemas
class StartScrapeRequest(BaseModel):
    """Request to start a scrape task."""
    url: HttpUrl


class TaskResponse(BaseModel):
    """Task status response."""
    task_id: int
    state: str
    url: str
    error: str | None = None
    result: dict | None = None
    message: str | None = None

    class Config:
        from_attributes = True


@router.post(
    "/start",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a scrape task",
    description="Create a new scrape task. Processing happens in background.",
)
async def start_scrape(
    request: StartScrapeRequest,
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
    url_str = str(request.url)

    result = await admit_scrape_task(user, url_str, db)

    if isinstance(result, AdmissionError):
        if result.error_type == AdmissionErrorType.ALREADY_HAS_ACTIVE_TASK:
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

    # Queue background processing
    background_tasks.add_task(
        execute_scrape_pipeline,
        task_id=task.id,
        user_id=user.id,
    )

    return TaskResponse(
        task_id=task.id,
        state=task.state.value,
        url=task.url,
        message="Task queued for processing",
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
    )


@router.get(
    "/tasks/current",
    response_model=TaskResponse | None,
    summary="Get current active task",
    description="Get the user's current non-terminal task, if any.",
)
async def get_current_task(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskResponse | None:
    """Get user's current active task."""
    result = await db.execute(
        select(ScrapeTask).where(
            ScrapeTask.user_id == user.id,
            ScrapeTask.state.notin_(TERMINAL_STATES),
        )
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
    )

