"""Browser session endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.session import (
    BrowserSessionCreate,
    BrowserSessionResponse,
    parse_cookies_raw,
)
from app.services import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post(
    "",
    response_model=BrowserSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a browser session",
)
async def create_session(
    payload: BrowserSessionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BrowserSessionResponse:
    try:
        cookies = parse_cookies_raw(payload.cookies_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    session = await session_service.create_session(
        db,
        user,
        name=payload.name,
        domain=payload.domain,
        cookies=cookies,
        user_agent=payload.user_agent,
        expires_at=payload.expires_at,
    )
    await db.commit()
    return BrowserSessionResponse.model_validate(session)


@router.get(
    "",
    response_model=list[BrowserSessionResponse],
    summary="List browser sessions",
)
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BrowserSessionResponse]:
    sessions = await session_service.list_sessions(db, user)
    return [BrowserSessionResponse.model_validate(s) for s in sessions]


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    summary="Delete a browser session",
)
async def delete_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    deleted = await session_service.delete_session(db, user, session_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
