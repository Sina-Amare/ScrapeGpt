"""
Health check endpoints.

These endpoints are used by:
- Load balancers for health checks
- Kubernetes liveness/readiness probes
- Monitoring systems

Health check endpoints should be fast and not require authentication.
"""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.services.readiness import check_db_ready


router = APIRouter(tags=["Health"])


# -----------------------------------------------------------------------------
# Response Schemas
# -----------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Health check response schema."""
    status: str
    environment: str
    version: str


class HealthDetailResponse(BaseModel):
    """Detailed health check response with component status."""
    status: str
    environment: str
    version: str
    database: str
    

# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Basic health check",
    description="Returns the basic health status of the API. Use this for simple uptime monitoring.",
)
async def health_check() -> HealthResponse:
    """
    Basic health check endpoint.
    
    This endpoint is intentionally simple and fast.
    It doesn't check database connectivity - use /health/ready for that.
    
    Returns:
        HealthResponse: Status information
    """
    return HealthResponse(
        status="healthy",
        environment=settings.ENVIRONMENT,
        version="0.1.0",
    )


@router.get(
    "/health/ready",
    response_model=HealthDetailResponse,
    summary="Readiness check with dependencies",
    description="Checks if the API and all dependencies are ready. Returns 503 if not ready.",
    responses={
        200: {"description": "Ready to receive traffic"},
        503: {"description": "Not ready - DB unavailable or migrations pending"},
    },
)
async def readiness_check(
    db: AsyncSession = Depends(get_db),
) -> HealthDetailResponse:
    """
    Readiness check endpoint.

    Returns 200 ONLY if instance is safe to receive traffic:
    - Database is connected
    - Core schema/migrations are compatible

    Returns 503 if any dependency is unavailable.

    Use this for Kubernetes readiness probes or load balancer health checks.
    """
    readiness = await check_db_ready(db, settings.READINESS_TIMEOUT_SECONDS)
    is_ready = readiness.ready

    response_data = {
        "status": "ready" if is_ready else "not_ready",
        "environment": settings.ENVIRONMENT,
        "version": "0.1.0",
        "database": readiness.code,
    }

    if not is_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response_data,
        )

    return HealthDetailResponse(**response_data)


@router.get(
    "/health/live",
    status_code=status.HTTP_200_OK,
    summary="Liveness check",
    description="Simple liveness probe. Returns 200 if the process is alive.",
)
async def liveness_check() -> dict:
    """
    Liveness check endpoint.
    
    This is the simplest possible health check.
    If this returns, the process is alive.
    
    Use this for Kubernetes liveness probes.
    
    Returns:
        dict: Simple alive status
    """
    return {"alive": True}
