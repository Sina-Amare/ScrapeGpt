"""
Scraping Pydantic schemas for request/response validation.
"""

from pydantic import BaseModel, Field, HttpUrl


class ScrapeRequest(BaseModel):
    """Schema for scrape request."""
    url: HttpUrl = Field(..., description="URL to scrape")
    selector: str | None = Field(
        default=None,
        description="Optional CSS selector to extract specific content"
    )


class ScrapeResponse(BaseModel):
    """Schema for scrape response."""
    success: bool = Field(..., description="Whether scrape was successful")
    url: str = Field(..., description="URL that was scraped")
    title: str | None = Field(default=None, description="Page title")
    content: str | None = Field(default=None, description="Extracted content")
    credits_remaining: int = Field(..., description="User's remaining credits")
    message: str | None = Field(default=None, description="Status message")


class ScrapeError(BaseModel):
    """Schema for scrape error response."""
    success: bool = Field(default=False)
    url: str
    error: str
    credits_remaining: int
