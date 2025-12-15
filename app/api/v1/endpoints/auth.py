"""
Authentication endpoints for user registration, login, and token refresh.

Endpoints:
    POST /auth/register - Register a new user
    POST /auth/login - Login via form (OAuth2 compatible) or JSON
    POST /auth/refresh - Refresh access token using refresh token
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
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
async def register(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """
    Register a new user.

    Creates a new user account and returns JWT tokens for immediate login.

    Args:
        request: Registration data (email, password)
        db: Database session

    Returns:
        AuthResponse: User data and JWT tokens

    Raises:
        HTTPException 400: If email already exists
    """
    # Check if email already exists
    result = await db.execute(
        select(User).where(User.email == request.email)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Create new user
    user = User(
        email=request.email,
        hashed_password=hash_password(request.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

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
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Login a user using OAuth2 password flow.

    Note: 'username' field accepts email address.
    This format is required for Swagger UI compatibility.

    Args:
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
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if account is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
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
async def refresh_token(
    request: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Refresh the access token.

    Uses a valid refresh token to generate a new access token
    without requiring re-authentication.

    Args:
        request: Refresh token data
        db: Database session

    Returns:
        TokenResponse: New access and refresh tokens

    Raises:
        HTTPException 401: If refresh token is invalid or expired
    """
    # Verify refresh token
    payload = verify_token(request.refresh_token, token_type="refresh")

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user still exists and is active
    user_id = int(payload.sub)
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    # Generate new tokens
    access_token = create_access_token(subject=user.id)
    new_refresh_token = create_refresh_token(subject=user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )
