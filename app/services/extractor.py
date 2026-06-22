"""Deterministic extraction from saved project specs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

try:  # markdownify is a declared dependency; degrade to plain text if absent.
    from markdownify import markdownify as _html_to_markdown_raw
except Exception:  # pragma: no cover - defensive
    _html_to_markdown_raw = None

from app.models.job import ExtractionMode, ExtractionSpec, Project

logger = logging.getLogger(__name__)


@dataclass
class ExtractedPayload:
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _selected_fields(spec: ExtractionSpec) -> list[dict[str, Any]]:
    return [field for field in spec.fields or [] if field.get("selected")]


def _field_key(field: dict[str, Any]) -> str:
    return str(field.get("user_label") or field.get("label") or field.get("name") or "field")


def _text(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(separator=" ", strip=True)).strip()


def _element_value(tag: Tag, field_type: str, source_url: str) -> str | None:
    field_type = field_type.lower()
    if field_type in {"url", "link"}:
        href = tag.get("href")
        if href:
            return urljoin(source_url, str(href))
    if field_type in {"image", "img"}:
        for attr in ("src", "data-src", "data-original", "srcset"):
            value = tag.get(attr)
            if value:
                first = str(value).split(",")[0].strip().split(" ")[0]
                return urljoin(source_url, first)
    for attr in ("content", "value", "title", "alt", "aria-label"):
        value = tag.get(attr)
        if value:
            return str(value).strip()
    text = _text(tag)
    return text or None


def _coerce_value(value: str | None, field_type: str) -> Any:
    if value is None:
        return None
    field_type = field_type.lower()
    if field_type == "number":
        cleaned = re.sub(r"[^0-9.,+-]", "", value).replace(",", "")
        try:
            return float(cleaned) if "." in cleaned else int(cleaned)
        except ValueError:
            return value
    if field_type == "boolean":
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "available", "in stock"}:
            return True
        if lowered in {"false", "no", "0", "unavailable", "out of stock"}:
            return False
    return value


def _relative_selector(selector: str, repeated_selector: str | None) -> str:
    if not repeated_selector:
        return selector
    stripped = selector.strip()
    repeated = repeated_selector.strip()
    if stripped.startswith(repeated):
        stripped = stripped[len(repeated) :].strip()
        if stripped.startswith(">"):
            stripped = stripped[1:].strip()
        return stripped or selector
    return selector


# Bare formatting/text-wrapper tags whose absence just means the text sits in
# the parent. Dropping a trailing one of these is safe (same content); dropping
# a value-bearing element like ``a``/``img``/``input`` would change WHAT is
# extracted (a link's href, an image's src), so those are never relaxed.
_SAFE_RELAX_TAGS = {
    "p", "span", "div", "b", "strong", "em", "i", "small", "label", "font",
    "mark", "u",
}


def relaxed_selectors(selector: str) -> list[str]:
    """Progressively shorter selectors by dropping only trailing *bare
    text-wrapper* tokens.

    AI selectors often over-specify a descendant that is absent in some cells —
    e.g. ``td:nth-child(3) p`` when the value sits as direct text in the ``td``.
    Relaxing to ``td:nth-child(3)`` recovers it. Stops at the first trailing
    token that is not a bare wrapper tag (e.g. ``a`` or ``p.title``), so a link
    selector is never silently turned into its parent's text. Returns the
    original first, then each safe shorter prefix.
    """
    tokens = (selector or "").split()
    out = [" ".join(tokens)] if tokens else []
    while len(tokens) > 1 and tokens[-1] in _SAFE_RELAX_TAGS:
        tokens = tokens[:-1]
        out.append(" ".join(tokens))
    return out


def _select_values(scope: BeautifulSoup | Tag, field: dict[str, Any], source_url: str) -> tuple[list[str | None], list[str]]:
    selector = field.get("selector")
    if not selector:
        return [], [f"{_field_key(field)} has no selector."]
    # Try the selector, then relaxed variants if it matches nothing, so an
    # over-specified descendant (e.g. a missing "<p>") still extracts the cell.
    elements: list[Any] = []
    for candidate in relaxed_selectors(str(selector)):
        try:
            elements = scope.select(candidate)
        except Exception as exc:
            return [], [f"{_field_key(field)} selector is invalid: {exc}"]
        if elements:
            break
    field_type = str(field.get("type") or "string")
    return [_element_value(element, field_type, source_url) for element in elements], []


def sample_selector_values(
    html: str,
    *,
    repeated_item_selector: str | None,
    selector: str,
    field_type: str = "string",
    max_rows: int = 25,
) -> list[str | None]:
    """Return the per-row value a CSS selector extracts, exactly as extraction
    reads it (row-relative scoping + relaxed selectors).

    Used to VERIFY a candidate/repaired selector against fetched HTML before
    trusting it. Returns ``[]`` when the HTML can't be parsed or the row/selector
    doesn't apply — the caller treats an empty result as "unverifiable".
    """
    if not selector:
        return []
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        return []
    field = {"selector": str(selector), "type": field_type}
    if repeated_item_selector:
        try:
            rows = soup.select(str(repeated_item_selector))[:max_rows]
        except Exception:
            return []
        scoped = dict(field)
        scoped["selector"] = _relative_selector(
            str(selector), str(repeated_item_selector)
        )
        out: list[str | None] = []
        for row in rows:
            values, _warnings = _select_values(row, scoped, "")
            out.append(next((v for v in values if v not in (None, "")), None))
        return out
    values, _warnings = _select_values(soup, field, "")
    return values[:max_rows]


def _extract_from_repeated_containers(
    soup: BeautifulSoup,
    *,
    source_url: str,
    project: Project,
    spec: ExtractionSpec,
    fields: list[dict[str, Any]],
    max_records: int,
) -> list[ExtractedPayload]:
    analysis = project.analysis or {}
    repeated_selector = analysis.get("repeated_item_selector")
    if not repeated_selector:
        return []
    try:
        containers = soup.select(str(repeated_selector))[:max_records]
    except Exception:
        return []
    if not containers:
        return []

    payloads: list[ExtractedPayload] = []
    for container in containers:
        raw: dict[str, Any] = {"source_url": source_url}
        normalized: dict[str, Any] = {"source_url": source_url}
        warnings: list[str] = []
        present = 0
        missing_required = False
        for field in fields:
            selector = field.get("selector")
            if selector:
                scoped = dict(field)
                scoped["selector"] = _relative_selector(str(selector), str(repeated_selector))
            else:
                scoped = field
            values, field_warnings = _select_values(container, scoped, source_url)
            warnings.extend(field_warnings)
            value = next((item for item in values if item not in (None, "")), None)
            key = _field_key(field)
            raw[key] = value
            normalized[key] = _coerce_value(value, str(field.get("type") or "string"))
            if value not in (None, ""):
                present += 1
            elif field.get("required"):
                missing_required = True
                warnings.append(f"{key} is required but missing on this record.")
        if present and not missing_required:
            payloads.append(ExtractedPayload(raw, normalized, warnings))
    return payloads


def _extract_by_field_index(
    soup: BeautifulSoup,
    *,
    source_url: str,
    fields: list[dict[str, Any]],
    max_records: int,
) -> list[ExtractedPayload]:
    values_by_key: dict[str, tuple[dict[str, Any], list[str | None]]] = {}
    global_warnings: list[str] = []
    row_count = 0
    for field in fields:
        values, warnings = _select_values(soup, field, source_url)
        global_warnings.extend(warnings)
        row_count = max(row_count, len(values))
        values_by_key[_field_key(field)] = (field, values)

    row_count = min(row_count, max_records)
    payloads: list[ExtractedPayload] = []
    for index in range(row_count):
        raw: dict[str, Any] = {"source_url": source_url}
        normalized: dict[str, Any] = {"source_url": source_url}
        warnings = list(global_warnings)
        present = 0
        missing_required = False
        for key, (field, values) in values_by_key.items():
            value = values[index] if index < len(values) else None
            raw[key] = value
            normalized[key] = _coerce_value(value, str(field.get("type") or "string"))
            if value not in (None, ""):
                present += 1
            elif field.get("required"):
                missing_required = True
                warnings.append(f"{key} is required but missing on this record.")
        minimum_present = 1 if len(fields) <= 2 else 2
        if present >= minimum_present and not missing_required:
            payloads.append(ExtractedPayload(raw, normalized, warnings))
    return payloads


def _norm_text(s: str) -> str:
    """Normalize a label/header for fuzzy column matching."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _best_data_table(soup: BeautifulSoup) -> Tag | None:
    """Pick the largest real data table (most rows x columns, >=2 cols)."""
    best: Tag | None = None
    best_score = 0
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        cols = max((len(r.find_all(["td", "th"])) for r in rows), default=0)
        if cols < 2:
            continue
        score = len(rows) * cols
        if score > best_score:
            best_score = score
            best = table
    return best


def _table_headers(table: Tag) -> list[str]:
    """Raw header texts for a table, or [] when it has no header row."""
    header_row = None
    thead = table.find("thead")
    if thead:
        header_row = thead.find("tr")
    if header_row is None:
        first = table.find("tr")
        if first is not None and first.find("th") is not None:
            header_row = first
    if header_row is None:
        return []
    return [_text(c) for c in header_row.find_all(["th", "td"])]


# Common unit-abbreviation synonyms so e.g. a "kcal" column maps to a
# "Calories" field. Kept deliberately small and unambiguous.
_HEADER_ALIASES = {
    "kcal": "calories",
    "kj": "kilojoules",
}


def _match_tokens(s: str) -> set[str]:
    """Word tokens of a label, expanded with known unit synonyms."""
    tokens = {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t}
    return tokens | {_HEADER_ALIASES[t] for t in tokens if t in _HEADER_ALIASES}


def _column_match_score(field_key: str, header: str) -> int:
    """Score a field<->header match. 0 means "do not match".

    Strict on purpose: short headers (id, no, kj, cal) must not match by loose
    substring, which previously produced plausible-but-wrong columns. Order:
    exact normalized equality > shared whole word/token > length-guarded substring.
    """
    fk, hk = _norm_text(field_key), _norm_text(header)
    if not fk or not hk:
        return 0
    if fk == hk:
        return 100
    shared = _match_tokens(field_key) & _match_tokens(header)
    if shared:
        return 50 + sum(len(t) for t in shared)
    # Length-guarded substring only: avoids "cal" in "physical", "id" in "video".
    short, long = (hk, fk) if len(hk) <= len(fk) else (fk, hk)
    if len(short) >= 5 and short in long:
        return 10
    return 0


def _map_fields_to_columns(
    fields: list[dict[str, Any]], headers: list[str], n_cols: int
) -> list[int | None]:
    """Assign each selected field a table column: best scored header, then position."""
    assignments: list[int | None] = [None] * len(fields)
    if headers:
        scored: list[tuple[int, int, int]] = []
        for fi, fld in enumerate(fields):
            key = _field_key(fld)
            for hi, header in enumerate(headers):
                if hi >= n_cols:
                    continue
                score = _column_match_score(key, header)
                if score > 0:
                    scored.append((score, fi, hi))
        # Greedy best-first; each field and header used at most once.
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        used_fields: set[int] = set()
        used_headers: set[int] = set()
        for _score, fi, hi in scored:
            if fi in used_fields or hi in used_headers:
                continue
            assignments[fi] = hi
            used_fields.add(fi)
            used_headers.add(hi)
    used_cols = {a for a in assignments if a is not None}
    free_cols = [c for c in range(n_cols) if c not in used_cols]
    fi = 0
    for i in range(len(fields)):
        if assignments[i] is None and fi < len(free_cols):
            assignments[i] = free_cols[fi]
            fi += 1
    return assignments


def _extract_from_tables(
    soup: BeautifulSoup,
    *,
    source_url: str,
    fields: list[dict[str, Any]],
    max_records: int,
) -> list[ExtractedPayload]:
    """Structure-based fallback: read a data table by column, ignoring AI selectors.

    Runs only when selector-based extraction found nothing, so pages whose
    real markup differs from the AI's guessed selectors (e.g. a <table> with
    unexpected class names) still extract. Columns are matched to the selected
    fields by header text, falling back to field/column order.
    """
    table = _best_data_table(soup)
    if table is None:
        return []
    headers = _table_headers(table)
    data_rows = [r for r in table.find_all("tr") if r.find_all("td")]
    if not data_rows:
        return []
    n_cols = max(len(r.find_all(["td", "th"])) for r in data_rows)
    assignments = _map_fields_to_columns(fields, headers, n_cols)

    payloads: list[ExtractedPayload] = []
    for tr in data_rows[:max_records]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        raw: dict[str, Any] = {"source_url": source_url}
        normalized: dict[str, Any] = {"source_url": source_url}
        present = 0
        for fld, col in zip(fields, assignments):
            key = _field_key(fld)
            value = _text(cells[col]) or None if col is not None and col < len(cells) else None
            raw[key] = value
            normalized[key] = _coerce_value(value, str(fld.get("type") or "string"))
            if value not in (None, ""):
                present += 1
        if present:
            payloads.append(ExtractedPayload(raw, normalized))
    if payloads:
        logger.info(
            "extractor.table_fallback_used",
            extra={"record_count": len(payloads), "columns": n_cols},
        )
    return payloads


# Page chrome that is never primary content — removed before content extraction.
# Tag-based only (safe): class/id heuristics risk nuking real content containers.
_CONTENT_CHROME_TAGS = (
    "script", "style", "noscript", "template", "nav", "header", "footer",
    "aside", "form", "iframe", "svg",
)
# Containers likely to hold the main article/content, best-first.
_CONTENT_MAIN_SELECTORS = (
    "main", "article", "[role=main]", "#content", "#main-content", "#main",
    ".content", ".article", ".post", ".entry-content", ".post-content",
)
# Below this many characters content is treated as "tiny" and a warning is added.
_MIN_CONTENT_TEXT_CHARS = 200


def _strip_content_chrome(scope: Tag | BeautifulSoup) -> None:
    """Remove scripts/nav/header/footer/aside/etc. in place (tag-based only)."""
    for el in scope.find_all(_CONTENT_CHROME_TAGS):
        el.decompose()


def _readable_text(node: Tag | BeautifulSoup | None) -> str:
    """Text with block-level line breaks preserved (headings/paragraphs/list items
    on their own lines) and intra-line whitespace collapsed."""
    if node is None:
        return ""
    raw = node.get_text(separator="\n")
    lines = [re.sub(r"[ \t \r\f\v]+", " ", ln).strip() for ln in raw.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


# Decorative / non-content noise removed before Markdown conversion. Heading
# anchor links ("¶" pilcrows), aria-hidden decorations and explicitly hidden
# nodes would otherwise leak into the readable output as junk or duplicates.
_CONTENT_NOISE_SELECTORS = (
    "a.headerlink",
    ".headerlink",
    "[aria-hidden=true]",
    "[hidden]",
)


def _strip_content_noise(scope: Tag | BeautifulSoup) -> None:
    """Remove heading-anchor pilcrows and hidden/decorative nodes in place."""
    for selector in _CONTENT_NOISE_SELECTORS:
        try:
            for el in scope.select(selector):
                el.decompose()
        except Exception:
            continue


def _code_language(el: Tag) -> str:
    """Best-effort source language for a ``<pre>`` block, read from the common
    ``language-xxx`` / ``lang-xxx`` / ``highlight-xxx`` class conventions used by
    Pygments, highlight.js, Prism, MkDocs, Sphinx and Docusaurus."""
    nodes: list[Tag | None] = [el, el.parent]
    nodes.extend(el.find_all("code"))
    for node in nodes:
        if node is None:
            continue
        for cls in (node.get("class") or []):
            for prefix in ("language-", "lang-", "highlight-"):
                if cls.startswith(prefix) and len(cls) > len(prefix):
                    return cls[len(prefix):]
    return ""


def _html_to_markdown(node: Tag | BeautifulSoup | None) -> str:
    """Convert a content container to clean Markdown.

    Preserves heading levels, lists, links, emphasis and — critically — fenced
    code blocks, so syntax-highlighted code is not shredded one token per line
    (the failure mode of a plain ``get_text`` dump). Falls back to plain readable
    text if the converter is unavailable or raises.
    """
    if node is None:
        return ""
    if _html_to_markdown_raw is None:
        return _readable_text(node)
    try:
        md = _html_to_markdown_raw(
            str(node),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
            escape_asterisks=False,
            escape_underscores=False,
            escape_misc=False,
            code_language_callback=_code_language,
        )
    except Exception:
        return _readable_text(node)
    # markdownify emits generous blank lines; trim trailing whitespace and
    # collapse runs of 3+ newlines to a single blank line.
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def _densest_content_container(soup: BeautifulSoup) -> Tag | None:
    """The main/article/content container with the most text, or None."""
    best: Tag | None = None
    best_len = 0
    for selector in _CONTENT_MAIN_SELECTORS:
        try:
            matches = soup.select(selector)
        except Exception:
            continue
        for el in matches:
            if not isinstance(el, Tag):
                continue
            length = len(el.get_text(" ", strip=True))
            if length > best_len:
                best, best_len = el, length
    return best


def _extract_content(
    soup: BeautifulSoup,
    *,
    source_url: str,
    spec: ExtractionSpec,
    fields: list[dict[str, Any]],
) -> list[ExtractedPayload]:
    """One readable content record per page (CONTENT mode).

    Strips page chrome, prefers the configured primary selector only when it
    yields enough text, otherwise picks the densest main/article container (body
    as a last resort), and preserves block-level line breaks. Optional metadata
    fields are still read from the ORIGINAL document so a ``<title>``/``<h1>`` in
    the header is not lost to chrome removal. Returns ``[]`` for truly empty
    content (so the caller's zero-record gate fires); very short content is
    returned with a warning rather than presented as a clean success.
    """
    warnings: list[str] = []
    # Work on a copy so chrome removal never disturbs metadata-field extraction.
    work = BeautifulSoup(str(soup), "lxml")
    _strip_content_chrome(work)
    # Drop pilcrow anchors / hidden duplicates before container selection so they
    # neither skew density scoring nor leak into the Markdown output.
    _strip_content_noise(work)

    selector = (spec.content_config or {}).get("primary_selector")
    scope: Tag | BeautifulSoup | None = None
    if selector:
        candidate: Tag | None = None
        try:
            matches = work.select(str(selector))
            candidate = matches[0] if matches else None
        except Exception as exc:
            warnings.append(f"Primary content selector is invalid: {exc}")
        # Honor the selector only when it actually yields enough text; otherwise
        # fall through to density-based selection (a tiny/empty match is worse
        # than the best main container).
        if candidate is not None and (
            len(candidate.get_text(" ", strip=True)) >= _MIN_CONTENT_TEXT_CHARS
        ):
            scope = candidate

    if scope is None:
        scope = (
            _densest_content_container(work)
            or work.find("main")
            or work.find("article")
            or work.body
            or work
        )

    text = _html_to_markdown(scope)
    if text and len(text) < _MIN_CONTENT_TEXT_CHARS:
        warnings.append(
            "Extracted content is very short — the page may be mostly navigation, "
            "an image/app, or rendered by JavaScript."
        )

    raw: dict[str, Any] = {"source_url": source_url, "content": text}
    normalized: dict[str, Any] = {"source_url": source_url, "content": text}
    for field in fields:
        values, field_warnings = _select_values(soup, field, source_url)
        warnings.extend(field_warnings)
        value = next((item for item in values if item not in (None, "")), None)
        key = _field_key(field)
        raw[key] = value
        normalized[key] = _coerce_value(value, str(field.get("type") or "string"))
    return [ExtractedPayload(raw, normalized, warnings)] if text else []


def extract_records_from_html(
    html: str,
    *,
    source_url: str,
    project: Project,
    spec: ExtractionSpec,
    max_records: int = 1000,
) -> list[ExtractedPayload]:
    """Execute the saved extraction spec against one HTML document."""
    soup = BeautifulSoup(html, "lxml")
    fields = _selected_fields(spec)
    if spec.mode == ExtractionMode.CONTENT:
        return _extract_content(soup, source_url=source_url, spec=spec, fields=fields)
    if not fields:
        return []

    grouped = _extract_from_repeated_containers(
        soup,
        source_url=source_url,
        project=project,
        spec=spec,
        fields=fields,
        max_records=max_records,
    )
    if grouped:
        return grouped
    indexed = _extract_by_field_index(soup, source_url=source_url, fields=fields, max_records=max_records)
    if indexed:
        return indexed
    # Last resort: the AI's selectors matched nothing. Read a real data table by
    # structure so weak/wrong selectors don't mean zero records.
    return _extract_from_tables(soup, source_url=source_url, fields=fields, max_records=max_records)
