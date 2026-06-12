"""Heuristic resolution of extraction mode for the 'Let ScrapeGPT decide' option.

When the user does not explicitly choose STRUCTURED or CONTENT at analyze time,
the mode must still be decided *before* analysis runs, because ``extraction_mode``
selects which analysis schema the analyzer requests (it is fixed at project
creation). Rather than silently defaulting every "decide" submission to
STRUCTURED, this module makes a cheap, transparent guess from the URL alone:
content-like destinations (code repositories, docs sites, articles, blogs,
wikis) lean CONTENT; everything else defaults to STRUCTURED.

This is intentionally a low-confidence heuristic. Analysis can still surface
warnings if the guess is wrong, and the user can re-create the project in the
other mode. It does not call the network or the LLM.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Hosts whose pages are almost always prose/document content rather than
# row-like records. Matched on the exact host or any subdomain of it.
_CONTENT_HOST_HINTS = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "readthedocs.io",
    "readthedocs.org",
    "medium.com",
    "substack.com",
    "dev.to",
    "wikipedia.org",
    "stackoverflow.com",
)

# Path fragments that strongly suggest document/content pages.
_CONTENT_PATH_HINTS = (
    "/docs",
    "/doc/",
    "/documentation",
    "/blog",
    "/article",
    "/wiki",
    "/readme",
    "/guide",
    "/manual",
    "/knowledge",
    "/help/",
    "/posts/",
    "/post/",
    "/news/",
)

# File suffixes that are document content.
_CONTENT_SUFFIXES = (".md", ".rst", ".txt", ".adoc")


def infer_extraction_mode_from_url(url: str) -> str:
    """Guess ``"CONTENT"`` or ``"STRUCTURED"`` from the URL. Defaults to STRUCTURED.

    Pure and side-effect free; safe to call before any fetch.
    """
    try:
        parts = urlsplit(url.strip().lower())
    except (ValueError, AttributeError):
        return "STRUCTURED"

    host = parts.netloc.split("@")[-1].split(":")[0]
    path = parts.path

    if any(host == hint or host.endswith("." + hint) for hint in _CONTENT_HOST_HINTS):
        return "CONTENT"
    if path.endswith(_CONTENT_SUFFIXES):
        return "CONTENT"
    if any(hint in path for hint in _CONTENT_PATH_HINTS):
        return "CONTENT"
    return "STRUCTURED"


def resolve_extraction_mode(url: str, explicit: str | None) -> str:
    """Return the explicit mode when the user chose one, otherwise infer from the URL."""
    if explicit:
        return explicit
    return infer_extraction_mode_from_url(url)
