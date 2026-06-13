"""DOM summary builder: strips noise and extracts structural signals for LLM analysis."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

_NOISE_TAGS = {"script", "style", "noscript", "head", "meta", "link", "svg", "iframe"}
_MAX_SUMMARY_CHARS = 10000

# --- HTML quality assessment ------------------------------------------------
# Cheap (no full parse) gate so undecodable/empty fetches never reach the LLM
# (which would hallucinate selectors) or the analysis cache.
_QUALITY_SAMPLE_CHARS = 20000
# Compressed/binary bytes decoded with errors="replace" become mostly U+FFFD;
# real HTML has ~0%. 5% cleanly separates the two without false positives.
_BINARY_RATIO_THRESHOLD = 0.05


@dataclass
class HtmlQuality:
    """Verdict on fetched text: usable HTML, undecodable binary, or empty/structureless."""

    label: str  # "ok" | "binary" | "structureless"
    replacement_ratio: float
    tag_count: int
    text_length: int
    reasons: list[str] = field(default_factory=list)

    @property
    def is_binary(self) -> bool:
        return self.label == "binary"

    @property
    def is_usable(self) -> bool:
        return self.label == "ok"


def assess_html_quality(html: str) -> HtmlQuality:
    """Classify fetched text before it reaches the DOM summary / LLM.

    "binary"       — high ratio of replacement/control bytes (wrong or undecoded
                     compression/encoding). Caller should fail PAGE_DECODE_FAILED
                     or retry via the stealth browser.
    "structureless"— decoded cleanly but essentially empty (no tags / no text).
                     Caller should treat as FETCH_HTML_QUALITY_FAILED after retries.
    "ok"           — usable.
    """
    if not html or not html.strip():
        return HtmlQuality("structureless", 0.0, 0, 0, ["empty response body"])

    sample = html[:_QUALITY_SAMPLE_CHARS]
    bad = sum(
        1
        for ch in sample
        if ch == "�" or ord(ch) == 0 or (ord(ch) < 0x20 and ch not in "\t\n\r\f")
    )
    ratio = bad / len(sample)
    if ratio > _BINARY_RATIO_THRESHOLD:
        return HtmlQuality(
            "binary",
            ratio,
            0,
            0,
            [f"{ratio:.0%} undecodable bytes — wrong or undecoded compression/encoding"],
        )

    tag_count = len(re.findall(r"<[a-zA-Z!/][^>]*>", sample))
    text_only = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    # Conservative: only flag genuinely empty responses, not sparse JS shells
    # (those are already handled by the fetcher's sparse -> browser fallback).
    if tag_count < 2 and len(text_only) < 30:
        return HtmlQuality(
            "structureless", ratio, tag_count, len(text_only), ["no HTML structure or text"]
        )
    return HtmlQuality("ok", ratio, tag_count, len(text_only))
_MAX_HEADINGS = 8
_MAX_LINKS = 12
_MAX_REPEAT_CLASSES = 15
_MAX_TABLES = 3
_MAX_DATA_ATTRS = 20


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


def _selector_for_sample(tag: Tag) -> str:
    classes = tag.get("class", [])
    if isinstance(classes, list) and classes:
        return tag.name + "." + ".".join(str(cls) for cls in classes[:2])
    tag_id = tag.get("id")
    if tag_id:
        return f"{tag.name}#{tag_id}"
    return tag.name


def _repeated_container_samples(soup: BeautifulSoup, classes: list[str]) -> list[str]:
    samples: list[str] = []
    for cls in classes[:5]:
        tag = soup.find(class_=cls)
        if not isinstance(tag, Tag):
            continue
        html = re.sub(r"\s+", " ", str(tag))[:900]
        samples.append(f"Selector hint: {_selector_for_sample(tag)}\nHTML sample: {html}")
    return samples


def _table_samples(soup: BeautifulSoup) -> list[str]:
    tables: list[str] = []
    for index, table in enumerate(soup.find_all("table")[:_MAX_TABLES], start=1):
        if not isinstance(table, Tag):
            continue
        rows = []
        for row in table.find_all("tr")[:3]:
            cells = [
                _text(cell)
                for cell in row.find_all(["th", "td"])
                if isinstance(cell, Tag) and _text(cell)
            ]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            tables.append(f"Table {index} sample:\n" + "\n".join(f"  {row}" for row in rows))
    return tables


def _data_attributes(soup: BeautifulSoup) -> list[str]:
    attrs: dict[str, str] = {}
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        for key, value in tag.attrs.items():
            if not key.startswith("data-") or key in attrs:
                continue
            attrs[key] = str(value)[:120]
            if len(attrs) >= _MAX_DATA_ATTRS:
                break
        if len(attrs) >= _MAX_DATA_ATTRS:
            break
    return [f"{key}={value!r}" for key, value in attrs.items()]


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
        samples = _repeated_container_samples(soup, repeated)
        if samples:
            parts.append("Repeated container HTML samples:\n" + "\n\n".join(samples))

    # Table samples
    tables = _table_samples(soup)
    if tables:
        parts.append("Table samples:\n" + "\n\n".join(tables))

    # Data attributes
    data_attrs = _data_attributes(soup)
    if data_attrs:
        parts.append("Data attributes found:\n  " + "\n  ".join(data_attrs))

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
