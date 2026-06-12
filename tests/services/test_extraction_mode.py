"""Tests for the 'Let ScrapeGPT decide' extraction-mode heuristic."""

import pytest

from app.services.extraction_mode import (
    infer_extraction_mode_from_url,
    resolve_extraction_mode,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/anthropics/anthropic-sdk-python",
        "https://GitHub.com/owner/repo/blob/main/README.md",
        "https://example.readthedocs.io/en/latest/",
        "https://en.wikipedia.org/wiki/Web_scraping",
        "https://blog.example.com/posts/why-css-selectors",
        "https://example.com/docs/getting-started",
        "https://example.com/guide/setup",
        "https://example.com/notes.md",
    ],
)
def test_content_like_urls_infer_content(url: str) -> None:
    assert infer_extraction_mode_from_url(url) == "CONTENT"


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.com/products?page=2",
        "https://example.com/listings/apartments",
        "https://directory.example.com/companies",
        "https://example.com/",
        "not a url",
        "",
    ],
)
def test_other_urls_default_to_structured(url: str) -> None:
    assert infer_extraction_mode_from_url(url) == "STRUCTURED"


def test_resolve_prefers_explicit_choice_over_heuristic() -> None:
    # An explicit choice always wins, even when the URL looks content-like.
    assert resolve_extraction_mode("https://github.com/owner/repo", "STRUCTURED") == "STRUCTURED"
    assert resolve_extraction_mode("https://shop.example.com/products", "CONTENT") == "CONTENT"


def test_resolve_falls_back_to_heuristic_when_unspecified() -> None:
    assert resolve_extraction_mode("https://github.com/owner/repo", None) == "CONTENT"
    assert resolve_extraction_mode("https://shop.example.com/products", None) == "STRUCTURED"
    assert resolve_extraction_mode("https://shop.example.com/products", "") == "STRUCTURED"
