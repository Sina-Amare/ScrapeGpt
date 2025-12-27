"""
URL scraping service with timeout enforcement.

Fetches and extracts content from URLs.
"""

import logging

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings


logger = logging.getLogger(__name__)

# Scraping timeout (60 seconds)
SCRAPE_TIMEOUT = 60.0


class ScrapeError(Exception):
    """Raised when scraping fails."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def scrape_url(url: str) -> str:
    """
    Fetch and extract text content from URL.

    Args:
        url: URL to scrape

    Returns:
        Extracted text content

    Raises:
        ScrapeError: On any scraping failure
    """
    logger.info("scrape.started", extra={"url": url})

    try:
        async with httpx.AsyncClient(
            timeout=SCRAPE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                url,
                headers={"User-Agent": settings.USER_AGENT},
            )
            response.raise_for_status()

        # Parse HTML
        soup = BeautifulSoup(response.text, "lxml")

        # Extract title
        title = soup.title.string if soup.title else ""

        # Remove non-content elements
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()

        # Extract text
        text = soup.get_text(separator="\n", strip=True)

        # Limit content length
        if len(text) > 50000:
            text = text[:50000]

        content = f"Title: {title}\n\n{text}" if title else text

        logger.info(
            "scrape.completed",
            extra={"url": url, "content_length": len(content)},
        )

        return content

    except httpx.TimeoutException:
        logger.error("scrape.timeout", extra={"url": url})
        raise ScrapeError(f"Scraping timeout after {SCRAPE_TIMEOUT}s")

    except httpx.HTTPStatusError as e:
        logger.error(
            "scrape.http_error",
            extra={"url": url, "status_code": e.response.status_code},
        )
        raise ScrapeError(
            f"HTTP error {e.response.status_code}",
            status_code=e.response.status_code,
        )

    except httpx.RequestError as e:
        logger.error("scrape.network_error", extra={"url": url, "error": str(e)})
        raise ScrapeError(f"Network error: {str(e)}")

    except Exception as e:
        logger.error("scrape.unexpected_error", extra={"url": url, "error": str(e)})
        raise ScrapeError(f"Scraping failed: {str(e)}")
