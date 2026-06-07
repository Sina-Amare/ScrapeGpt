"""DOM summary builder: strips noise and extracts structural signals for LLM analysis."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup, Tag

_NOISE_TAGS = {"script", "style", "noscript", "head", "meta", "link", "svg", "iframe"}
_MAX_SUMMARY_CHARS = 4000
_MAX_HEADINGS = 8
_MAX_LINKS = 12
_MAX_REPEAT_CLASSES = 5


def _text(el: Tag) -> str:
    return el.get_text(separator=" ", strip=True)[:200]


def _jsonld_data(soup: BeautifulSoup) -> list[dict]:
    results = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, dict):
                results.append({k: data[k] for k in ("@type", "name", "description") if k in data})
        except Exception:
            pass
    return results[:3]


def _repeated_containers(soup: BeautifulSoup) -> list[str]:
    """Find CSS classes used on 3+ sibling elements — likely list/grid containers."""
    class_counts: dict[str, int] = {}
    for tag in soup.find_all(True):
        classes = tag.get("class", [])
        if isinstance(classes, list):
            for cls in classes:
                class_counts[cls] = class_counts.get(cls, 0) + 1

    repeated = [
        cls for cls, count in sorted(class_counts.items(), key=lambda x: -x[1]) if count >= 3
    ]
    return repeated[:_MAX_REPEAT_CLASSES]


def _pagination_candidates(soup: BeautifulSoup) -> list[str]:
    """Find links or buttons that look like pagination controls."""
    candidates = []
    pg_words = re.compile(r"(next|prev|page|more|load|→|»)", re.I)
    for tag in soup.find_all(["a", "button"]):
        text = _text(tag) if isinstance(tag, Tag) else ""
        href = tag.get("href", "") if isinstance(tag, Tag) else ""
        cls = " ".join(tag.get("class", [])) if isinstance(tag, Tag) else ""
        if pg_words.search(text) or pg_words.search(str(href)) or pg_words.search(cls):
            selector = tag.name
            if cls:
                selector += "." + cls.split()[0]
            candidates.append(selector)
    return list(dict.fromkeys(candidates))[:4]


def build_dom_summary(html: str, url: str = "") -> str:
    """
    Build a compact structural summary of an HTML page for LLM analysis.

    Returns a plain-text summary capped at _MAX_SUMMARY_CHARS.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove noise
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    parts: list[str] = []

    # URL context
    if url:
        parts.append(f"URL: {url}")

    # Title
    title = soup.find("title")
    if title and isinstance(title, Tag):
        parts.append(f"Title: {_text(title)}")

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and isinstance(meta_desc, Tag):
        content = meta_desc.get("content", "")
        if content:
            parts.append(f"Meta description: {str(content)[:200]}")

    # Headings
    headings = []
    for h in soup.find_all(["h1", "h2", "h3"])[:_MAX_HEADINGS]:
        if isinstance(h, Tag):
            headings.append(f"  [{h.name}] {_text(h)}")
    if headings:
        parts.append("Headings:\n" + "\n".join(headings))

    # JSON-LD structured data
    jsonld = _jsonld_data(soup)
    if jsonld:
        parts.append("Structured data (JSON-LD): " + json.dumps(jsonld, separators=(",", ":")))

    # Repeated container classes
    repeated = _repeated_containers(soup)
    if repeated:
        parts.append("Repeated element classes (likely list/grid items): " + ", ".join(f".{c}" for c in repeated))

    # Sample links
    links = []
    for a in soup.find_all("a", href=True)[:_MAX_LINKS]:
        if isinstance(a, Tag):
            text = _text(a)
            href = str(a.get("href", ""))[:80]
            if text:
                links.append(f"  {text!r} -> {href}")
    if links:
        parts.append("Sample links:\n" + "\n".join(links))

    # Pagination candidates
    pg = _pagination_candidates(soup)
    if pg:
        parts.append("Pagination candidates: " + ", ".join(pg))

    # Body text snippet
    body = soup.find("body")
    if body and isinstance(body, Tag):
        body_text = body.get_text(separator=" ", strip=True)
        snippet = re.sub(r"\s+", " ", body_text)[:600]
        if snippet:
            parts.append(f"Body text snippet: {snippet}")

    summary = "\n\n".join(parts)
    return summary[:_MAX_SUMMARY_CHARS]
