# ScrapeGPT — Product, UX, and Strategy Review

**Review date:** June 9, 2026  
**Phase context:** written before Phase 2.5 implementation; updated notes mark what Phase 2.5 addressed.  
**Scope:** crawl boundaries, AI analysis context, competitive positioning, advanced settings UX, future risks.

> **Post-review update (June 10, 2026):** R1 (legacy `/scrape` SSRF) and R2 (crawl-page lease reaper) were fixed in the reliability hardening pass. See `docs/STATUS.md` and `docs/learning/12_reliability_hardening.md` for details. The remaining open risks (R3–R10) stand as written.

---

## Executive Summary

ScrapeGPT's core architectural thesis — *AI understands the site once, code extracts every page* — is sound and differentiating. The product has a working end-to-end loop. The weakness identified in this review was that the system treated crawl discovery as a technical same-origin problem, while users think in terms of datasets, pages, pagination, categories, and sites.

**Phase 2.5 addressed the most critical gap:** crawl scope is now a first-class object, frontier preview exists, and extraction requires explicit scope confirmation for non-current-page modes. The remaining work described here feeds into Phase 3 and beyond.

---

## 1. Crawl Scope Problem

> **Phase 2.5 status:** Addressed. `CrawlScope` object added to `ExtractionSpec`. Scope confirmation gate enforced. Frontier preview implemented. The items in this section now describe the design rationale behind Phase 2.5 and the remaining gaps.

### 1.1 The problem

Before Phase 2.5, the crawler at `app/services/project_extraction.py` called `discover_same_site_links()` for every fetched page, with the only filter being same-origin URL matching plus optional free-text glob patterns. There was no semantic notion of "this dataset", "this pagination chain", or "this page only".

**The calories.info example:** submitting `https://www.calories.info/food/potato-products` would cause the crawler to follow links to Pizza, Meat, Beer, Fruit — all valid same-origin URLs but outside the user's intended dataset. This was the default behavior, not a bug to be patched. Users had no first-class way to declare their intent.

### 1.2 What Phase 2.5 built

A `CrawlScope` JSONB object on `ExtractionSpec` with:

| Field | Meaning |
|-------|---------|
| `mode` | `CURRENT_PAGE` \| `PAGINATION` \| `DATASET` \| `FULL_SITE` |
| `status` | `AI_SUGGESTED` \| `USER_CONFIRMED` \| `SYSTEM_DEFAULTED` |
| `seed_url` | Entry point for the scope |
| `max_pages` / `max_depth` | Resource bounds (not scope definition) |
| `include_patterns` / `exclude_patterns` | Optional URL constraints |
| `pagination` | Pagination URL pattern and selector |
| `link_rules` | Role-based link inclusion rules |
| `ai_recommendation` | AI-suggested scope with confidence and evidence |
| `user_confirmed_at` | Timestamp of explicit user acceptance |

**Scope modes** and their behavior:
- **`CURRENT_PAGE`**: no link discovery; only the seed URL is fetched. Conservative default for new projects.
- **`PAGINATION`**: follows only URLs matching common pagination patterns (`?page=`, `/page/N`, `pagination.url_pattern`). Category and navigation links excluded.
- **`DATASET`**: follows pagination plus `include_patterns` and `link_rules` with role `detail`/`dataset`. Requires user confirmation.
- **`FULL_SITE`**: broad same-origin BFS. Preserves legacy behavior. Requires explicit user confirmation.

**Confirmation gate** (`app/services/crawl_scope.py`):
- `CURRENT_PAGE` does not require confirmation.
- `PAGINATION`, `DATASET`, `FULL_SITE` require `status = USER_CONFIRMED` before extraction proceeds.
- `assert_scope_confirmed()` raises `ScopeConfirmationError` → HTTP 409 `SCOPE_NOT_CONFIRMED`.

**Frontier preview** (`app/services/frontierpreview.py`):
- `POST /projects/{id}/frontier-preview` classifies links from the seed page using `classify_links_for_scope()`.
- Shows included/excluded URLs with reason codes before extraction commits.
- Preview and extraction share the same classifier, so what the preview shows is what the crawler follows.

### 1.3 What remains for future phases

- **Path-based pagination** (`/food/potato-products/2`) is not reliably detected without explicit `pagination.url_pattern`. The `p=` query token heuristic is broad.
- **AI-suggested scope** currently requires the user to set scope manually; Phase 3 should let AI pre-fill scope based on page type and analysis confidence.
- **`llms.txt` and `sitemap.xml`** as discovery sources — not yet implemented. Sites that publish `llms.txt` are declaring their own "intended scope".
- **Template fingerprinting**: when scope includes detail pages or sibling listing pages, different page templates need different selector sets. Currently one spec is applied globally.
- **Per-project cost cap**: the user has no pre-crawl estimate of how many pages will be fetched.

### 1.4 Design principles to carry forward

- **Scope is user intent, not a technical URL filter.** `url_patterns` are an implementation detail. Scope is the product concept.
- **AI recommends, user confirms.** Broad crawling must never be the silent default. The user sees evidence (frontier preview) before committing.
- **`page_limit` is a safety budget, not a scope definition.** Do not rely on it as the primary constraint on what gets extracted.
- **Default conservatively.** `CURRENT_PAGE` is the right default for new structured extraction projects until the user or AI suggests otherwise.

---

## 2. AI Analysis Context

### 2.1 What the AI currently sees

`build_dom_summary()` (`app/services/dom_summary.py`) produces a 10,000-character structural excerpt:
- Page title and meta description
- Up to 8 H1–H3 headings
- Up to 3 JSON-LD objects (only `@type`, `name`, `description`)
- Up to 15 repeated CSS classes with HTML samples
- Up to 3 tables (3 rows each)
- Up to 20 `data-*` attributes
- Up to 12 anchor links
- 600-character body text snippet
- Total cap: 10,000 characters

### 2.2 What information is lost

For the highest-value extraction targets (e-commerce, B2B directories, listings):
- **Price components**: original price, sale price, discount percentage — nested in sibling elements
- **Stock/availability badges**: expressed as `data-*` attributes on 4th+ child elements
- **JSON-LD metadata beyond `@type|name|description`**: `offers`, `price`, `availability`, `brand`, `sku`, `aggregateRating`, `breadcrumbs`
- **Repeated container variance**: the summary takes *one* HTML sample per class; the AI never sees how the second and third items differ
- **Hydration JSON**: `<script>` tags with embedded product state are stripped
- **Microdata** (`itemscope`/`itemprop`/`itemtype`)

The current summary is sufficient for simple listing/content pages. It underperforms on e-commerce, marketplaces, and sites with rich embedded metadata.

### 2.3 Recommended direction

**Rich structural summary** (3–5× the current cap, 30,000–50,000 characters):
- All JSON-LD blocks, unfiltered
- Up to 5 HTML samples per repeated container at different positions (not one)
- All headings (no cap)
- All `data-*` attributes across the full page
- Microdata + OpenGraph + Twitter Card extraction
- A "structural fingerprint" of repeated containers: count, position range, child-element distribution

This trades a small cost multiplier for substantially better schema quality. Since AI is called once per project, the absolute cost is still low.

Bump `ANALYZER_VERSION` (currently `"1"` in `app/services/analyzer.py`) when the summary changes — the cache key includes it, so old analyses are not incorrectly served for new summaries.

---

## 3. Competitive Positioning

### 3.1 Current differentiators

| Differentiator | Strength | Notes |
|---|---|---|
| BYOK + self-hosted | Real | No SaaS markup, full data ownership. Privacy-sensitive and cost-sensitive users. |
| AI analyzes once, code extracts all pages | Real | 500-page job = ~1–3 LLM calls vs 500+ for Crawl4AI/ScrapeGraphAI |
| Open-source | Real | No vendor lock-in; auditable |
| First-class crawl scope (Phase 2.5) | Real | No other open-source BYOK tool has this UX primitive |

### 3.2 Competitor comparison

| Tool | BYOK | Scope primitives | Non-technical UX | Self-host | Open source |
|------|------|------------------|------------------|-----------|-------------|
| **Firecrawl** | No | Rich (includePaths/excludePaths/maxDepth/sitemap) | Partial | Partial | Core only |
| **Crawl4AI** | Yes | Basic (depth/breadth/same_domain) | No | Yes | Yes |
| **ScrapeGraphAI** | Yes | Basic (depth/breadth/include/exclude) | No | Yes | Yes |
| **Browse AI** | No | Visual workflow (no explicit scope) | Yes | No | No |
| **Apify** | No | Very rich (globs/pseudoURLs/strategies) | No | No | Actors only |
| **ScrapeGPT** | Yes | Semantic modes + confirmation + preview | Partial (Phase 3) | Yes | Yes |

ScrapeGPT's niche: *BYOK + self-hosted + semantic scope + deterministic extraction + non-technical UX (Phase 3)*. No competitor combines all five.

### 3.3 What to build for defensible differentiation

1. **Dataset quality observability** — per-selector yield rates, per-field success/missing tracking, yield-drop alerts. This requires deep extraction-pipeline integration that SaaS tools cannot easily match.
2. **Semantic crawl scope with AI pre-fill** — let the AI suggest scope mode based on page type, then require user confirmation. The confirmed scope becomes the extraction contract.
3. **Self-healing selectors** — when per-selector yield drops below threshold, surface failing pages and ask AI to re-suggest. Evidence-based, not silent.

What **not** to build for differentiation: markdown output, basic JSON/CSV export, JS rendering, multi-format export — these are commodity features every competitor has.

---

## 4. Advanced Settings UX Review

> **Phase 2.5 status:** Partially addressed. `workflow_mode` was hidden from the primary UI. "What are you extracting?" promoted to primary field. "Advanced settings" renamed to "Connection and rendering". Export format moved to Results. Page limit renamed "Safety limit". The remaining items below are Phase 3 targets.

### 4.1 Current settings and recommended final design

| Setting | Current location | Problem | Recommended |
|---------|-----------------|---------|-------------|
| Data type (STRUCTURED/CONTENT) | New Extraction advanced | Good concept, wrong framing | Present as "What do you want?" — "Rows in a table" vs "Content for knowledge base"; move to primary flow |
| Page rendering (AUTO/STATIC/BROWSER) | New Extraction advanced | Implementation detail | Default auto; surface only in troubleshooting ("Page looks empty? Try browser rendering") |
| AI provider | New Extraction advanced | Distraction from data goal | Show "Using: [default provider]" only; primary home is Providers settings |
| `workflow_mode` | Hidden in Phase 2.5 | Was implementation vocabulary | Replace with "Review first" / "Extract now" based on confidence gate |
| Page limit / Safety limit | Extraction section | Good, Phase 2.5 renamed | Keep as safety budget near Extract, paired with scope estimate |
| Export format | Moved to Results in Phase 2.5 | ✓ Fixed | Done |
| Raw Advanced JSON | "Developer details" in Phase 2.5 | ✓ Fixed | Done |

### 4.2 Long-term UX flow

The product flow should feel like:

```
URL
  → Understand Data (AI analysis → scope suggestion → user confirms intent)
  → Choose Fields (field selection, selector preview)
  → Preview (seed-page selector preview + frontier preview for non-CURRENT_PAGE)
  → Extract (scope confirmed; safety budget visible)
  → Results (paginated records; export format choice)
```

Technical controls (render mode, provider override, page limit, url patterns) belong in a secondary "Connection and rendering" section and never in the primary task flow.

---

## 5. Future Risks

| Rank | Risk | Severity | Phase 2.5 mitigated? |
|------|------|----------|----------------------|
| 1 | Wrong crawl scope creates incorrect datasets | Very High | **Yes** — scope gate + frontier preview + confirmation |
| 2 | Poor selector quality silently drops fields | Very High | Partial — trust summary added; no yield tracking or repair yet |
| 3 | AI misses fields due to lossy DOM summary | High | No — still 10k char cap, one sample per container |
| 4 | Excessive crawling wastes resources | High | Partial — scope modes bound discovery; no pre-crawl estimate yet |
| 5 | Silent undercounts erode user trust | High | Partial — quality summary added; no yield ratio shown yet |
| 6 | Selector drift on site template changes | Medium-High | No — no scheduled re-crawl or drift detection |
| 7 | Multi-template sites produce inconsistent records | Medium | No — one spec applied globally |
| 8 | Crash mid-extraction strands pages | Medium | No — `lease_expires_at` still not swept by watchdog |
| 9 | Legacy `/scrape` SSRF vulnerability | High (security) | **Resolved** — SSRF validation added at endpoint, executor, and redirect-hop levels |
| 10 | Multi-worker deploys not supported | Medium | No — in-process BackgroundTasks + APScheduler still single-process |

### Top-priority items that remain open

~~**R1 — Fix legacy `/scrape` SSRF.**~~ **Resolved.** Fixed in reliability hardening: `validate_url()` + robots checks added at endpoint, executor, and redirect-hop levels.

~~**R2 — Add crawl page lease reaper.**~~ **Resolved.** Fixed in reliability hardening: `cleanup_expired_crawl_page_leases()` added to `app/services/watchdog.py`, runs every 60 s.

**R3 — Enrich the DOM summary.** Move to rich structural summary (5 container samples, full JSON-LD, microdata, no heading cap). Bump `ANALYZER_VERSION` to `"2"`.

**R4 — Add per-field yield tracking.** Surface "title: 100%, price: 47%" after extraction. Trigger `NEEDS_REVIEW` state when any selected field is below threshold.

~~**R5 — Add watchdog sweep for `DISCOVERING/EXTRACTING/EXPORTING` states.**~~ **Resolved.** `cleanup_stuck_projects()` added, handles all three states with configurable timeouts.

---

## 6. Recommendations for Phase 3 and Beyond

### Do next (Phase 3)

1. **Visual field selection**: render the seed page in a sandboxed iframe; user clicks elements; system generates CSS selectors from the DOM path. Technical users can override in Advanced.
2. **SSE live progress**: `GET /projects/{id}/stream` with per-page state events. Replaces polling with real-time feedback.
3. **Rich DOM summary** (§2.3 above).
4. **Per-selector yield tracking** + `NEEDS_REVIEW` state.
5. **Fix legacy `/scrape` SSRF** (security-critical, small effort).

### Do later (Phases 4–5)

- Structural normalization (date parsing, currency parsing, compound fields) — post-extraction, additive.
- RAG export formats (markdown, chunked JSONL, vector-DB-ready).
- Authenticated content (human-in-the-loop cookie paste, session management).
- Docker/docker-compose one-command setup.
- Redis-backed rate limiting for multi-worker safety.
- Concurrent extraction (`CRAWL_CONCURRENCY` setting is present but unused).

---

*End of review.*
