"""
Scraping endpoint with authentication and credit system.

This endpoint requires:
- Valid JWT authentication
- Available credits (checked before scraping)
- Credits are deducted after successful scrape
"""

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_credits, deduct_credit
from app.core.config import settings
from app.models.user import User
from app.schemas.scrape import ScrapeRequest, ScrapeResponse


router = APIRouter(prefix="/scrape", tags=["Scraping"])


@router.post(
    "",
    response_model=ScrapeResponse,
    summary="Scrape a webpage",
    description="Scrape content from a URL. Requires authentication and consumes 1 credit.",
)
async def scrape_url(
    request: ScrapeRequest,
    user: User = Depends(require_credits),
    db: AsyncSession = Depends(get_db),
) -> ScrapeResponse:
    """
    Scrape content from a URL.

    This endpoint:
    1. Validates user authentication
    2. Checks for available credits
    3. Fetches the URL content
    4. Extracts text/title
    5. Deducts 1 credit on success

    Args:
        request: Scrape request with URL and optional selector
        user: Authenticated user with credits (injected via dependency)
        db: Database session

    Returns:
        ScrapeResponse: Scraped content and remaining credits

    Raises:
        HTTPException 401: If not authenticated
        HTTPException 403: If no credits available
        HTTPException 400: If URL cannot be fetched
    """
    url_str = str(request.url)

    try:
        # Fetch the URL
        async with httpx.AsyncClient(
            timeout=settings.SCRAPE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                url_str,
                headers={"User-Agent": settings.USER_AGENT},
            )
            response.raise_for_status()

        # Parse HTML
        soup = BeautifulSoup(response.text, "lxml")

        # Extract title
        title = soup.title.string if soup.title else None

        # Extract content
        if request.selector:
            # Use CSS selector if provided
            elements = soup.select(request.selector)
            content = "\n".join(el.get_text(strip=True) for el in elements)
        else:
            # Get main text content
            # Remove script and style elements
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()
            content = soup.get_text(separator="\n", strip=True)
            # Limit content length
            content = content[:10000] if len(content) > 10000 else content

        # Deduct credit after successful scrape
        await deduct_credit(user, db)

        return ScrapeResponse(
            success=True,
            url=url_str,
            title=title,
            content=content,
            credits_remaining=user.credits_remaining,
            message="Scrape successful",
        )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request timed out after {settings.SCRAPE_TIMEOUT}s",
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to fetch URL: {e.response.status_code}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scraping failed: {str(e)}",
        )


@router.get(
    "/credits",
    summary="Get credit status",
    description="Check your remaining credits and time until reset.",
)
async def get_credits(
    user: User = Depends(require_credits),
) -> dict:
    """Get current credit status."""
    return {
        "credits_remaining": user.credits_remaining,
        "daily_limit": user.daily_credit_limit,
        "seconds_until_reset": user.seconds_until_reset,
    }
