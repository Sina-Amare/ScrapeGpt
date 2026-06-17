"""
ScrapeGPT - FastAPI Application Entry Point

This is the main entry point for the ScrapeGPT API.
It initializes the FastAPI application with all middleware,
routes, and lifecycle handlers.

Usage:
    # Development
    uvicorn app.main:app --reload
    
    # Production
    gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.log_context import (
    clear_context,
    set_request_context,
)
from app.core.logging_config import configure_logging
from app.db.database import close_db

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Application Lifespan
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.
    
    This handles startup and shutdown events:
    - Startup: Initialize logging, start scheduler
    - Shutdown: Stop scheduler, close connections, cleanup
    
    Args:
        app: The FastAPI application instance
        
    Yields:
        None: Control is passed to the application
    """
    # -------------------------------------------------------------------------
    # Startup — configure logging FIRST, before any other imports
    # -------------------------------------------------------------------------
    configure_logging()

    logger.info(
        "app.starting",
        extra={
            "app_name": settings.APP_NAME,
            "environment": settings.ENVIRONMENT,
            "debug": settings.DEBUG,
        },
    )

    # The watchdog must run in exactly one process. When RUN_SCHEDULER is false
    # (a non-scheduler worker in a multi-process deployment) skip both the
    # startup recovery sweep and the periodic scheduler so we don't get duplicate
    # sweeps or duplicate resume dispatch.
    if settings.RUN_SCHEDULER:
        # Recover state left stuck by a previous process death BEFORE scheduling
        # periodic sweeps, so a restart (by an external supervisor — which
        # production still requires) immediately recovers orphaned runs/projects.
        try:
            from app.services.watchdog import run_watchdog_once
            await run_watchdog_once()
            logger.info("watchdog.startup_sweep_complete")
        except Exception:
            logger.exception("watchdog.startup_sweep_failed")

        from app.core.scheduler import start_scheduler
        start_scheduler()
        logger.info("scheduler.started")
    else:
        logger.info("scheduler.disabled", extra={"reason": "RUN_SCHEDULER=false"})

    yield  # Application runs here

    # -------------------------------------------------------------------------
    # Shutdown
    # -------------------------------------------------------------------------
    logger.info("app.shutting_down", extra={"app_name": settings.APP_NAME})

    # Stop scheduler
    from app.core.scheduler import stop_scheduler
    stop_scheduler()

    # Close database connections
    await close_db()

    logger.info("app.shutdown_complete")


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    Application factory function.
    
    Creates and configures the FastAPI application with all
    middleware, routes, and settings.
    
    Returns:
        FastAPI: The configured application instance
    """
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "A professional web scraping API with AI capabilities. "
            "Features include JWT authentication, BYOK provider management, "
            "and async job processing."
        ),
        version="0.1.0",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )
    
    # -------------------------------------------------------------------------
    # Middleware
    # -------------------------------------------------------------------------

    # Request context middleware — binds request_id and logs
    # HTTP request/response events with timing.
    @app.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next,
    ):
        request_id = (
            request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        set_request_context(request_id=request_id)
        start = time.monotonic()
        try:
            response = await call_next(request)
            duration_ms = int(
                (time.monotonic() - start) * 1000
            )
            logger.info(
                "http.request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                },
            )
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            duration_ms = int(
                (time.monotonic() - start) * 1000
            )
            logger.error(
                "http.request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            clear_context()

    # CORS Middleware
    # Allows cross-origin requests from specified origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate Limiting Middleware
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.core.rate_limit import limiter

    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded, _rate_limit_exceeded_handler
    )
    
    # -------------------------------------------------------------------------
    # Routes
    # -------------------------------------------------------------------------
    
    # Include API v1 router
    app.include_router(
        api_v1_router,
        prefix=settings.API_V1_PREFIX,
    )
    
    # Prometheus metrics (no-op text when prometheus_client is not installed).
    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint():
        from fastapi import Response
        from app.core.metrics import CONTENT_TYPE_LATEST, render_latest
        return Response(content=render_latest(), media_type=CONTENT_TYPE_LATEST)

    # Root endpoint for basic info
    @app.get("/", include_in_schema=False)
    async def root():
        """Root endpoint with basic API information."""
        return {
            "name": settings.APP_NAME,
            "version": "0.1.0",
            "docs": "/docs" if settings.DEBUG else None,
            "health": f"{settings.API_V1_PREFIX}/health",
        }
    
    return app


# -----------------------------------------------------------------------------
# Create Application Instance
# -----------------------------------------------------------------------------

# This is the ASGI application instance that Uvicorn will serve
app = create_app()


# -----------------------------------------------------------------------------
# Development Server
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=settings.WORKERS if not settings.DEBUG else 1,
    )
