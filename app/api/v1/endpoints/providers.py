"""Provider configuration endpoints."""

import logging

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.rate_limit import PROVIDER_REVEAL_RATE_LIMIT, limiter
from app.core.security import verify_password
from app.models.user import User
from app.schemas.provider import (
    ProviderConfigCreate,
    ProviderConfigResponse,
    ProviderConfigUpdate,
    ProviderKeyRevealRequest,
    ProviderKeyRevealResponse,
    ProviderTestResponse,
)
from app.services import provider_service


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["Providers"])


async def _raise_provider_conflict(db: AsyncSession) -> None:
    await db.rollback()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Provider configuration conflict",
    )


async def _get_owned_provider_or_404(
    provider_id: int,
    user: User,
    db: AsyncSession,
):
    provider_config = await provider_service.get_provider_config(
        db,
        user_id=user.id,
        provider_config_id=provider_id,
    )
    if provider_config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider config not found",
        )
    return provider_config


@router.get(
    "",
    response_model=list[ProviderConfigResponse],
    summary="List provider configs",
)
async def list_providers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderConfigResponse]:
    """List the authenticated user's provider configs."""
    providers = await provider_service.list_provider_configs(db, user.id)
    return [ProviderConfigResponse.model_validate(provider) for provider in providers]


@router.post(
    "",
    response_model=ProviderConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create provider config",
)
async def create_provider(
    payload: ProviderConfigCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderConfigResponse:
    """Create a provider config. API key is encrypted and not returned here."""
    try:
        provider_config = await provider_service.create_provider_config(db, user.id, payload)
    except IntegrityError:
        await _raise_provider_conflict(db)
    return ProviderConfigResponse.model_validate(provider_config)


@router.patch(
    "/{provider_id}",
    response_model=ProviderConfigResponse,
    summary="Update provider config",
)
async def update_provider(
    provider_id: int,
    payload: ProviderConfigUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderConfigResponse:
    """Update a provider config. API key is write-only when supplied."""
    provider_config = await _get_owned_provider_or_404(provider_id, user, db)
    try:
        provider_config = await provider_service.update_provider_config(
            db,
            user_id=user.id,
            provider_config=provider_config,
            payload=payload,
        )
    except IntegrityError:
        await _raise_provider_conflict(db)
    return ProviderConfigResponse.model_validate(provider_config)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete provider config",
)
async def delete_provider(
    provider_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a provider config scoped to the authenticated user."""
    provider_config = await _get_owned_provider_or_404(provider_id, user, db)
    await provider_service.delete_provider_config(db, user.id, provider_config)


@router.post(
    "/{provider_id}/reveal-key",
    response_model=ProviderKeyRevealResponse,
    summary="Reveal provider API key",
    description="Return the decrypted API key after owner and password confirmation.",
)
@limiter.limit(PROVIDER_REVEAL_RATE_LIMIT)
async def reveal_provider_key(
    request: Request,
    provider_id: int,
    payload: ProviderKeyRevealRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderKeyRevealResponse:
    """Decrypt and return the stored API key after password confirmation."""
    provider_config = await _get_owned_provider_or_404(provider_id, user, db)
    if not verify_password(payload.password, user.hashed_password):
        logger.warning(
            "security.key_reveal_failed",
            extra={
                "user_id": user.id,
                "provider_config_id": provider_config.id,
                "reason": "invalid_password",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password confirmation failed",
        )

    try:
        api_key = provider_service.decrypt_api_key(
            provider_config.api_key_encrypted
        )
        logger.warning(
            "security.key_revealed",
            extra={
                "user_id": user.id,
                "provider_config_id": provider_config.id,
            },
        )
    except InvalidToken:
        logger.warning(
            "security.key_reveal_failed",
            extra={
                "user_id": user.id,
                "provider_config_id": provider_config.id,
                "reason": "decryption_error",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to decrypt API key. The encryption key has changed.",
        )
    return ProviderKeyRevealResponse(api_key=api_key)


@router.post(
    "/{provider_id}/test",
    response_model=ProviderTestResponse,
    summary="Test provider config",
)
async def test_provider(
    provider_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderTestResponse:
    """Test provider connectivity and structured JSON capability."""
    provider_config = await _get_owned_provider_or_404(provider_id, user, db)
    ok, flags, error = await provider_service.test_provider_config(db, provider_config)
    return ProviderTestResponse(
        ok=ok,
        provider_config_id=provider_config.id,
        capability_flags=flags,
        error=error,
    )
