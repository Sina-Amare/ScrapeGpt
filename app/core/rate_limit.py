"""
Rate limiting configuration for ScrapeGPT.

Implements per-user rate limiting to prevent abuse.
Uses slowapi (production-ready, based on flask-limiter).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

from app.core.config import settings
from app.core.security import verify_token


def get_user_identifier(request: Request) -> str:
    """
    Get rate limit key from request.

    Priority:
    1. Authenticated user ID (from JWT)
    2. IP address (for unauthenticated requests)
    """
    # Try to extract JWT from Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        payload = verify_token(token, token_type="access")
        if payload:
            return f"user:{payload.sub}"

    # Fall back to IP address
    return get_remote_address(request)


# Global limiter instance
limiter = Limiter(
    key_func=get_user_identifier,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
    # Default "memory://" is per-process. Point RATE_LIMIT_STORAGE_URI at a
    # shared store (e.g. redis://) to enforce limits across multiple workers.
    storage_uri=settings.RATE_LIMIT_STORAGE_URI,
)


# Rate limit decorators for specific endpoints
SCRAPE_RATE_LIMIT = f"{settings.RATE_LIMIT_SCRAPE_PER_MINUTE}/minute"
AUTH_RATE_LIMIT = f"{settings.RATE_LIMIT_AUTH_PER_MINUTE}/minute"
PROVIDER_REVEAL_RATE_LIMIT = f"{settings.RATE_LIMIT_AUTH_PER_MINUTE}/minute"
