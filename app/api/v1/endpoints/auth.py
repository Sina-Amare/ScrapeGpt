"""
Authentication endpoints for user registration, login, and token refresh.

Endpoints:
    POST /auth/register - Register a new user
    POST /auth/login - Login via form (OAuth2 compatible) or JSON
    POST /auth/refresh - Refresh access token using refresh token
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.rate_limit import limiter, AUTH_RATE_LIMIT
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_token,
)
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    TokenRefreshRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)


router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Register Endpoint
# -----------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Create a new user account with email and password.",
)
@limiter.limit(AUTH_RATE_LIMIT)
async def register(
    request: Request,
    payload: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """
    Register a new user.

    Creates a new user account and returns JWT tokens for immediate login.

    Args:
        request: FastAPI request used by the rate limiter
        payload: Registration data (email, password)
        db: Database session

    Returns:
        AuthResponse: User data and JWT tokens

    Raises:
        HTTPException 400: If email already exists
    """
    # Check if email already exists
    result = await db.execute(
        select(User).where(User.email == payload.email)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        logger.warning(
            "auth.register_failed",
            extra={"reason": "email_exists"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Create new user
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(
        "auth.register_success",
        extra={"user_id": user.id},
    )

    # Generate tokens
    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        tokens=TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
        ),
    )


# -----------------------------------------------------------------------------
# Login Endpoint (OAuth2 Compatible)
# -----------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login user",
    description="Login with username (email) and password. Returns JWT tokens.",
)
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Login a user using OAuth2 password flow.

    Note: 'username' field accepts email address.
    This format is required for Swagger UI compatibility.

    Args:
        request: FastAPI request used by the rate limiter
        form_data: OAuth2 form with username (email) and password
        db: Database session

    Returns:
        TokenResponse: JWT access and refresh tokens

    Raises:
        HTTPException 401: If credentials are invalid
        HTTPException 403: If account is deactivated
    """
    # Find user by email (username field contains email)
    result = await db.execute(
        select(User).where(User.email == form_data.username)
    )
    user = result.scalar_one_or_none()

    # Verify credentials
    if not user or not verify_password(
        form_data.password, user.hashed_password
    ):
        logger.warning(
            "auth.login_failed",
            extra={"reason": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if account is active
    if not user.is_active:
        logger.warning(
            "auth.login_failed",
            extra={
                "reason": "user_inactive",
                "user_id": user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    logger.info(
        "auth.login_success",
        extra={"user_id": user.id},
    )

    # Generate tokens
    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


# -----------------------------------------------------------------------------
# Token Refresh Endpoint
# -----------------------------------------------------------------------------

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    description="Get a new access token using a valid refresh token.",
)
@limiter.limit(AUTH_RATE_LIMIT)
async def refresh_token(
    request: Request,
    payload: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Refresh the access token.

    Uses a valid refresh token to generate a new access token
    without requiring re-authentication.

    Args:
        request: FastAPI request used by the rate limiter
        payload: Refresh token data
        db: Database session

    Returns:
        TokenResponse: New access and refresh tokens

    Raises:
        HTTPException 401: If refresh token is invalid or expired
    """
    # Verify refresh token
    token_payload = verify_token(payload.refresh_token, token_type="refresh")

    if not token_payload:
        logger.warning(
            "auth.token_refresh_failed",
            extra={"reason": "invalid_token"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user still exists and is active
    try:
        user_id = int(token_payload.sub)
    except ValueError:
        logger.warning(
            "auth.token_refresh_failed",
            extra={"reason": "invalid_subject"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid subject in token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        logger.warning(
            "auth.token_refresh_failed",
            extra={"reason": "user_not_found"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        logger.warning(
            "auth.token_refresh_failed",
            extra={
                "reason": "user_inactive",
                "user_id": user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    logger.info(
        "auth.token_refresh_success",
        extra={"user_id": user.id},
    )

    # Generate new tokens
    access_token = create_access_token(subject=user.id)
    new_refresh_token = create_refresh_token(subject=user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )
