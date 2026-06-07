"""
API v1 router aggregation.

This module combines all v1 endpoint routers into a single router
that is mounted at the API v1 prefix.

Usage:
    from app.api.v1.router import api_v1_router

    app.include_router(api_v1_router, prefix="/api/v1")
"""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, jobs, providers, scrape

# Create the main v1 router
api_v1_router = APIRouter()

# Include all endpoint routers
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(providers.router)
api_v1_router.include_router(scrape.router)
api_v1_router.include_router(jobs.router)


