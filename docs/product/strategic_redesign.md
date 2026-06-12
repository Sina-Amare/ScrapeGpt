# ScrapGPT — Strategic Redesign: Open-Source BYOK Extraction Platform

> **Last updated:** June 11, 2026. See `docs/reviews/` history in git for the reasoning behind specific decisions.
>
> **Implementation status as of June 11, 2026:** Phase 0, Phase 0.5, frontend v0, Phase 1 analysis jobs, the Project workflow (Analyze → Field Selection → Preview → Extract → Results), Phase 2 real extraction, Phase 2.5 crawl scope/frontier preview/trust signals, structured logging (stdlib + contextvars; `docs/learning/11_logging_observability.md`), and reliability hardening (legacy scrape SSRF at all levels, CrawlPage lease reaper, stuck-project watchdog, all-pages-failed semantics; `docs/learning/12_reliability_hardening.md`) are implemented. `/jobs` remains as a compatibility API over project rows. This document is the forward-looking roadmap for template intelligence, visual interaction, durable worker recovery, advanced exports, and authenticated-content phases. For the current runnable surface, see `docs/STATUS.md`.
>
> **Current Phase 2 state:** `build_dom_summary()` now includes repeated container HTML samples, table samples, `data-*` attributes, up to 15 repeated classes, and a 10,000-character cap. Preview and extraction now execute selectors against real fetched HTML. Remaining quality work is template detection, selector repair, visual selection, and durable multi-worker recovery.

## Context

The product vision has fundamentally shifted. The original design was a credit-gated SaaS backend with an internal billing layer. The new direction is an **open-source, self-hosted, BYOK (Bring Your Own Key)** AI-assisted web data extraction platform. Users run it locally or on their own VPS, connect their own AI provider credentials, and own all their data. No platform credits, no billing, no artificial limits — only configurable self-hosted resource controls.

The platform serves both **technical users** (who want full control over selectors, schemas, and pipelines) and **non-technical users** (who should be able to submit a URL, review AI suggestions, and extract data without knowing what a CSS selector is). Non-technical access is the default; technical controls are opt-in.

This document is a strategic plan: architecture, phased roadmap, tradeoffs, and risks. Not implementation tasks.

---

## 1. What the Product Actually Is

> Turn websites into clean, structured datasets or RAG-ready knowledge bases — using AI to understand, and deterministic code to extract.

Two output modes, both first-class:

- **Structured mode**: AI suggests extractable fields and selectors. User configures the schema. Deterministic CSS extraction produces tabular records (CSV, JSON, XLSX).
- **Content mode**: AI identifies content regions. Full-page text is extracted, cleaned, and chunked for embedding pipelines, vector databases, and knowledge bases.

The key distinction from other tools: **AI is the analyst, not the crawler.** AI analyzes site structure and suggests the extraction configuration. Deterministic code does the actual page-by-page extraction. This avoids the cost, latency, and unreliability of calling an LLM for every page.

---

## 2. Comparable Tools — Where the Gap Is

| Tool | AI Role | Self-Host | BYOK | Structured | RAG/Content | Non-Tech UX | Open Source |
|------|---------|-----------|------|------------|-------------|-------------|-------------|
| **Firecrawl** | Markdown conversion | Partial | No | Yes | Markdown only | No | Core only |
| **Crawl4AI** | LLM extraction per-crawl | Yes | Yes | Yes | Yes | No | Yes |
| **ScrapeGraphAI** | LLM graph agent (every page) | Yes | Yes | Yes | No | No | Yes |
| **Browse AI** | Robot training by clicking | No | No | Yes | No | Yes | No |
| **Apify** | Actor ecosystem | No | No | Yes | No | No | Actors only |
| **Crawlee** | None | Yes | N/A | No | No | No | Yes |
| **Jina Reader** | Markdown conversion | No | No | No | Markdown only | No | No |
| **Diffbot** | Proprietary NLP | No | No | Yes | No | No | No |

**Where the gap is:** No existing tool combines (1) visual, non-technical UX, (2) intelligent AI site analysis, (3) dual-mode output (structured + RAG-ready content), (4) deterministic CSS extraction engine, (5) full self-hosting, (6) BYOK multi-provider, (7) page-level checkpointing with crash recovery, in a single open-source platform.

Crawl4AI and ScrapeGraphAI call the LLM on every page — expensive, slow, and unreliable at scale. Browse AI has non-technical UX but is SaaS-only. Firecrawl is closest in product feel but is SaaS-first, converts pages to unstructured Markdown (losing field-level structure), and has no visual field selection. The architectural insight — **AI understands the site once, code extracts all pages** — is the right one, and no open-source self-hosted tool has executed it with a non-technical UX.

**The cost argument matters at scale:** A 500-page structured extraction with Firecrawl or Crawl4AI costs 500 LLM calls. With ScrapGPT it costs 1–3 (analysis + optional repairs). At 1,500 free requests/day on Google AI Studio, a user can analyze 500 sites per day rather than 3.

---

## 3. Core Architectural Decisions

### 3.1 Provider Abstraction: LiteLLM

Use **LiteLLM** as the provider abstraction layer. It supports 100+ providers (OpenAI, Anthropic, Gemini, OpenRouter, Mistral, Ollama, etc.) through a single unified API:

```python
await litellm.acompletion(model="gemini/gemini-1.5-pro", messages=[...], response_format={"type": "json_object"})
```

Adding a new provider requires zero code change in the platform.

**Important caveat on structured output:** JSON mode and response schema support varies by provider and model. GPT-4o is reliable; Anthropic uses tool-calling patterns; Gemini has edge cases; smaller/local models are often inconsistent. The platform must not assume uniform structured output support.

Mitigation:
- **Provider capability detection** at registration time: send a test structured-output prompt and validate the response schema.
- **LiteLLM JSON parsing pipeline** (every AI call goes through this):
  1. Attempt provider-native structured output (`response_format={"type":"json_object"}` or tool-calling, depending on `capability_flags`)
  2. If the provider rejects or doesn't support it, fall back to a strict text prompt appended with the target JSON schema and the instruction *"Output ONLY raw JSON conforming to this schema. No markdown, no preamble, no trailing text."*
  3. On any response, strip markdown fences and locate the outermost `{...}` or `[...]` only — ignore anything outside it
  4. Parse and validate against the expected Pydantic schema
  5. On validation failure, retry up to 3 times with a clarified prompt
  6. After 3 failures, surface the raw LLM response as an explicit user-facing error — never silently accept malformed or partially-repaired JSON

Alternative considered: Build custom provider abstraction. Rejected — LiteLLM is mature, actively maintained, and handles retry, fallback, and cost tracking we'd otherwise build ourselves.

**Provider free tier guidance (for documentation and onboarding):**

| Provider | Free tier RPD | Free tier RPM | Structured output | Verdict |
|----------|--------------|--------------|-------------------|---------|
| Google AI Studio (Gemini 2.0 Flash) | 1,500 | 15 | ✅ Schema-enforced | **Recommended default** |
| OpenRouter `:free` models | 50 (no deposit) / 1,000 ($10 deposit) | 20 (on paper) | ⚠️ Model-dependent | Testing/prototype only |
| Ollama (local) | Unlimited | CPU-bound | ⚠️ Model-dependent | Good for privacy, slow |
| OpenAI / Anthropic (paid) | — | — | ✅ Reliable | Best quality, paid only |

Because ScrapGPT calls AI at most a few times per project (not per page), the Google AI Studio free tier (1,500 RPD) is sufficient for meaningful daily usage even without any payment. This should be the recommended starting point in the onboarding docs. OpenRouter free tier is not recommended for production use due to endemic 429 errors and a 50 RPD hard cap that a single retry loop can exhaust.

### 3.2 AI Role: Used Sparingly, Not Per-Page

AI is called in at most five contexts — never once per extraction page:

1. **Site analysis (structured mode)**: AI analyzes the seed page's DOM summary to identify page type, suggest extractable fields and selectors, detect pagination, and estimate content volume. For sites with multiple page templates, AI may be called once per distinct template — not per page.

2. **Site analysis (content mode)**: AI identifies the primary content regions, heading hierarchy, and content density patterns. Returns chunking recommendations and content metadata rather than field selectors.

3. **Extraction setup refinement**: If the AI's initial confidence for a field selector is low, a second targeted call can sharpen the suggestion before the crawl begins.

4. **Selector repair** (optional, triggered on failure): If a previously working selector returns empty on N consecutive pages, the system can invoke AI to re-analyze and suggest replacements. Hard cap: max 3 repair attempts per field per job. After the cap, the field is marked `UNRELIABLE` and AI is not called again for it.

5. **Normalization** (optional, post-extraction): AI assists with parsing structurally ambiguous field values — date format detection, price string parsing, compound field splitting. AI must not rewrite, summarize, compress, or remove information. Structural parsing only.

AI cost stays proportional to the number of distinct site templates analyzed, not the number of pages extracted.

### 3.3 Extraction: Two Modes, Both Deterministic After Analysis

**Structured mode:** Once the user configures extraction fields, extraction is pure CSS selectors executed with lxml. No LLM per page. Fast, cheap, deterministic. Failures are debuggable.

**Content mode:** Full-page text is extracted using a readability algorithm (trafilatura or similar), preserving heading structure, metadata, and clean prose. No LLM per page. Output is document chunks with metadata, ready for embedding pipelines. Configurable chunking: by heading, by paragraph, or by character/token limit.

Both modes share the same crawl infrastructure (BFS, leases, checkpointing, challenge detection). The extraction step is the only thing that differs.

### 3.4 Job Architecture: Page-Level Checkpointing with DB-Backed Leases

Each crawl page is a database row. State is persisted per-page before any processing begins.

**The lease model** prevents pages from being stranded when a worker process crashes:

- When a worker claims a page, it sets `state=FETCHING, lease_expires_at=now()+30s`.
- The worker heartbeats every 10 seconds, extending `lease_expires_at`.
- If the process crashes, the heartbeat stops. The lease expires.
- A watchdog sweep (every 30 seconds) resets all pages where `state=FETCHING AND lease_expires_at < now()` back to `QUEUED`.
- On restart, the job resumes from the last committed page automatically.

This means no page is permanently lost to a crash. In-process background tasks are sufficient for single-instance self-hosted deployment; the lease model makes them crash-safe.

### 3.5 Data Preservation: Raw + Normalized, Never Lossy

Every extracted record stores both layers unconditionally:
- `raw_data JSONB`: exactly what the CSS selector returned (structured mode) or the full extracted text block (content mode). Unmodified. Always preserved.
- `normalized_data JSONB`: structurally cleaned version — parsed dates, extracted numeric values, split compound fields, standardized formats. Null until normalization runs.

**Normalization invariant:** Normalization is structural and additive — parse, standardize, split. It must never summarize, compress, rewrite for clarity, or remove information present in `raw_data`. A user reading `normalized_data` must be able to verify it against `raw_data` with no information loss.

Normalization is idempotent and reversible. `raw_data` is never touched.

### 3.7 DOM Summary Quality

The AI never sees the raw HTML. Before the LLM call, `build_dom_summary()` produces a compact structural excerpt that is sent instead. This is correct by design — sending full HTML would be expensive and noisy. But the current implementation is too thin for complex pages.

**Implemented Phase 2 improvements:**

- Max 8 headings and 12 sample links.
- Up to 15 repeated CSS classes.
- HTML samples from repeated containers.
- Table header/sample-row snippets.
- `data-*` attribute inspection.
- 600-character body text snippet.
- Total cap: 10,000 characters.

**Remaining risk:** For very complex pages, one seed-page summary can still miss secondary templates, late-rendered components, or fields only present after interaction. Phase 3 quality work should add template fingerprints, field-level success rates, and selector repair.

**Future improvements:**

| Improvement | Why it matters |
|-------------|----------------|
| Template-specific summaries | Listing, detail, search, and category pages need different field assumptions |
| Interaction-aware summaries | Some fields appear only after expanding accordions or tabs |
| Field-level selector diagnostics | Users need to know which selectors fail on which page templates |
| Visual selection fallback | Non-technical users need a non-CSS way to correct a bad selector |

These improvements build on the current `dom_summary.py` foundation without changing the AI-per-site-not-per-page architecture.

### 3.6 No Artificial Limits — Configurable Resource Controls

The credit system is completely removed. No `credits_remaining`, `daily_credit_limit`, `credits_reset_at`. No `system_state` table. No atomic credit deduction. No APScheduler credit reset job.

The "one active job per user" hard limit is also removed. For a self-hosted tool, resource constraints are an operator concern, not a platform policy. Replace with **configurable settings**:

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_CONCURRENT_JOBS_PER_USER` | 3 | Max active jobs per user simultaneously |
| `MAX_PAGES_PER_JOB` | 500 | Default page limit; user can override per-job |
| `CRAWL_CONCURRENCY` | 3 | Concurrent page fetches per job |
| `MIN_CRAWL_DELAY_MS` | 500 | Minimum delay between requests to the same domain |
| `JOB_QUEUE_DEPTH` | 10 | Max queued (not yet started) jobs per user |

The admission layer remains — it checks `MAX_CONCURRENT_JOBS_PER_USER` instead of the old one-job invariant. The partial unique index (from migration 003) is replaced with a count-based admission check.

---

## 4. What Survives From Current Codebase

**Keep:**

| Component | Notes |
|-----------|-------|
| Auth (JWT, bcrypt, register/login/refresh) | Solid, well-tested |
| State machine pattern | Keep pattern, redesign states |
| Admission service | Keep, strip credit check, replace invariant with count check |
| `task_state.py` transition pattern | Own session + `db.begin()` per transition |
| Watchdog | Keep pattern, update for new states + lease expiry sweep |
| APScheduler | Keep, remove credit reset job, add lease expiry job |
| `scraper.py` | Starting point; evolves into `fetcher.py` |
| Health/readiness endpoints | Keep as-is |
| Database pattern (asyncpg, sessions) | Keep as-is |
| Rate limiting (SlowAPI) | Keep as-is |
| CORS, config pattern | Keep |

**Remove or replace:**

| Component | Notes |
|-----------|-------|
| Credit system (all of it) | `credits_remaining`, `daily_limit`, reset logic, `system_state` table |
| Partial unique index (migration 003) | Replaced by count-based admission check |
| `llm_processor.py` (stub) | Replace with `provider_service.py` |
| `task_executor.py` | Redesign for new pipeline |
| `scrape_tasks.content` field | Move to seed CrawlPage |

---

## 5. New Data Model

### Users (modified)

Remove: `credits_remaining`, `daily_credit_limit`, `credits_reset_at`
Add: `default_provider_id` (FK to `provider_configs`, nullable)

### Provider Configs (new table)

```
provider_configs:
  id, user_id (FK), name (display name),
  provider (gemini/openai/anthropic/openrouter/etc),
  model (e.g. "gemini-1.5-pro"),
  api_key_encrypted (AES-256 Fernet — see encryption note),
  is_default, capability_flags JSONB,
  created_at, updated_at
```

**Encryption:** API keys are encrypted at rest using Fernet from the `cryptography` library. The encryption key is derived from `settings.PROVIDER_KEY_ENCRYPTION_SECRET` — a **separate setting from `SECRET_KEY`**, stored independently in `.env`. This separation means a compromised JWT secret does not expose stored provider keys.

**Key loss is unrecoverable:** Losing `PROVIDER_KEY_ENCRYPTION_SECRET` makes all stored provider API keys permanently unreadable. Users must re-enter them. The setup docs must warn about this explicitly and recommend backing up this value separately from the database.

**Key rotation:** To rotate the encryption secret, provide a management command that decrypts all keys with the old secret and re-encrypts with the new one before updating the setting. Do not update the setting without migrating the data first.

Normal provider responses **never include API keys** — not even masked — and key material is **never logged**. A user may explicitly reveal their own stored key only through a password-confirmed reveal endpoint; the frontend must keep the revealed value in local component state only, never localStorage, query cache, URLs, or logs. Log only `provider_config_id`, provider name, and operation status. The `capability_flags` field stores the provider capability test result (whether structured output, JSON mode, etc. are supported).

### Projects (implemented foundation)

`projects` is now the durable user-facing extraction object. The previous Phase 1 `jobs` table is migrated to `projects`; `/jobs` is kept as a temporary compatibility API.

Current project flow:

```
QUEUED
  ↓
ANALYZING
  ↓
AWAITING_SETUP | ANALYSIS_READY
  ↓
PREVIEWING
  ↓
PREVIEW_READY
  ↓
DISCOVERING
  ↓
EXTRACTING
  ↓
EXPORTING
  ↓
COMPLETED

FAILED | CANCELED are terminal from active paths.
PAUSED is reserved for later crawler recovery/resume.
```

Current project workflow tables:

- `extraction_specs`: user-selected fields/content settings, separate from raw AI analysis.
- `preview_results`: latest persisted selector preview for refresh/navigation safety.
- `crawl_pages`: project-scoped page rows with state/lease/retry fields for current crawl tracking and future durable recovery.
- `extracted_records`: `raw_data` plus optional `normalized_data`, source URL, warnings.
- `exports`: generated export metadata.

Important current limitation: extraction is a sequential in-process background task. Page rows are persisted and page failures are isolated, but true multi-worker claiming, heartbeats, and crash-resume lease recovery are still future work.

### Scrape Tasks / Legacy Jobs (modified)

Remove: `content` (moved to seed CrawlPage), credit-related fields
Add: `analysis JSONB`, `render_mode`, `final_url`, `provider_config_id` (FK), `pages_total`, `pages_extracted`, `records_total`, `access_basis`, **`extraction_mode` (STRUCTURED | CONTENT)**

`extraction_mode` is set at job creation and never changes. It determines which analysis schema the analyzer returns and which extraction path the crawl executor takes.

### Job State Machine

Current implemented Phase 1 states:

```
QUEUED
  ↓
ANALYZING
  ↓
ANALYSIS_READY | AWAITING_SETUP | FAILED | CANCELED

TERMINAL_STATES = {ANALYSIS_READY, AWAITING_SETUP, FAILED, CANCELED}
```

`workflow_mode=GUIDED` normally lands in `AWAITING_SETUP` after analysis completes.
`workflow_mode=FAST` lands in `ANALYSIS_READY` only when confidence is high and
warnings are empty; otherwise it also lands in `AWAITING_SETUP`. Phase 1 does not
start extraction yet.

Target state machine after Phase 2:

```
QUEUED
  ↓
ANALYZING             ← AI analyzes seed page(s)
  ↓                ↓
AWAITING_SETUP    (Fast Mode: skip with AI defaults)
  ↓                ↓
DISCOVERING   ←───┘   ← BFS crawl discovers all pages
  ↓
EXTRACTING            ← CSS extraction (structured) or content extraction (content)
  ↓
EXPORTING             ← Generate export files
  ↓
COMPLETED

FAILED | CANCELED  ← Terminal from any non-terminal state

TERMINAL_STATES = {COMPLETED, FAILED, CANCELED}
```

`AWAITING_SETUP` becomes the human gate for Guided Mode once Phase 2 adds review/setup.
In **Fast Mode** (`workflow_mode=FAST` on job creation), the job may transition directly
from ANALYZING to DISCOVERING using AI defaults — but only when the analysis meets the
confidence gate. If it does not, the job automatically falls back to AWAITING_SETUP
regardless of the workflow mode.

**Fast Mode confidence gate:** Fast Mode auto-start is permitted only when ALL of the following hold:
- Overall analysis confidence ≥ `FAST_MODE_CONFIDENCE_THRESHOLD` (default: 0.80, configurable)
- No major warnings (JS rendering required, auth required, CAPTCHA detected, site structure ambiguous)
- Pagination is detected with reasonable confidence (not estimated as 1 page when the site clearly has more)
- All candidate fields have individual confidence ≥ 0.70

If any condition fails after Phase 2, the job moves to AWAITING_SETUP and the UI
clearly explains why: *"We're not confident enough to extract automatically. Please
review before starting."*

NORMALIZING is removed as a state. Normalization is a background operation on already-extracted records, not a blocking pipeline stage.

### Crawl Pages (new table)

One row per URL discovered during a job.

```
crawl_pages:
  id, task_id (FK), url, normalized_url,
  state (QUEUED | FETCHING | FETCHED | EXTRACTING | EXTRACTED |
         FAILED | SKIPPED | CHALLENGE_REQUIRED | AUTH_REQUIRED |
         RATE_LIMITED | BLOCKED),
  content_hash, depth, is_seed,
  lease_expires_at (for crash recovery),
  retry_count, error, block_reason,
  created_at, updated_at
```

Unique constraint: `(task_id, normalized_url)`.

Required indexes: composite `(state, lease_expires_at)` for the watchdog sweep; composite `(task_id, state)` for the worker queue query. Without these, both degrade to full table scans as pages accumulate.

Challenge states are **not** FAILED — the job continues extracting other pages. `RATE_LIMITED` pages are auto-retried with exponential backoff. `CHALLENGE_REQUIRED` and `AUTH_REQUIRED` require human action (Phase 5) or can be skipped.

### Extraction Specs (new table)

One row per job. Stores: `fields JSONB` (structured mode), `url_patterns JSONB`, `chunking_config JSONB` (content mode), `page_limit`, `export_format`, `crawl_scope`, `normalization_enabled`, `access_basis`.

`url_patterns` is an ordered list of `{"pattern": "/products/*", "template": "detail"}` mappings. During extraction, each page URL is matched against patterns in order; the first match selects the field config to use. If no pattern matches and extraction returns mostly empty fields, the page records an `extraction_warning`. Automatic template detection via DOM structural fingerprinting is deferred — URL pattern routing is the Phase 2 mechanism.

### Extracted Records (new table)

One row per extracted item.

Structured mode: `raw_data JSONB` (selector output), `normalized_data JSONB`, `source_url`, `page_id` (FK), `extraction_warnings JSONB`.

Content mode: `raw_data JSONB` (full extracted text block + metadata), `normalized_data JSONB` (null or cleaned), `content_blocks JSONB` (chunked text array with positions), `source_url`, `page_id` (FK).

### Exports (new table)

Tracks generated export files: `format`, `file_path`, `record_count`, `spec_hash`, `generated_at`.

Formats: CSV, JSON, JSONL, XLSX (structured mode); Markdown, chunked JSONL, vector-DB-ready JSONL (content mode).

### Analysis Cache (new table)

Caches AI analysis results by `content_hash`. Avoids re-calling AI for pages with identical content.

---

## 6. API Surface

### Implemented endpoints

- `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`
- `GET /health`, `GET /health/ready`, `GET /health/live`

| Method | Path | Purpose |
|--------|------|---------|
| `GET / POST / PATCH / DELETE` | `/providers` | CRUD for user's provider configs |
| `POST` | `/providers/{id}/test` | Test provider connectivity + capability detection |
| `POST` | `/providers/{id}/reveal-key` | Reveal own key after password confirmation |
| `POST` | `/jobs` | Submit URL + `extraction_mode` + `workflow_mode` + `render_mode` → return 202 |
| `GET` | `/jobs` | User's analysis job history |
| `GET` | `/jobs/{id}` | Job status + analysis + fetch metadata |
| `POST` | `/jobs/{id}/cancel` | Cancel `QUEUED` or `ANALYZING` job |
| `DELETE` | `/jobs/{id}` | Delete terminal analysis job |
| `POST` | `/scrape/start` | Legacy scrape task pipeline, still available but not primary |
| `GET` | `/scrape/tasks`, `/scrape/tasks/{id}`, `/scrape/tasks/current` | Legacy scrape task list/detail/current |
| `DELETE` | `/scrape/tasks/{id}` | Delete terminal legacy scrape task |

### Project endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/projects/analyze` | Analyze a URL and create a project |
| `GET` | `/projects` | List user projects |
| `GET` | `/projects/{id}` | Project detail, spec, preview, progress, and analysis |
| `PATCH` | `/projects/{id}/spec` | Save field/content selection, page limit, export format |
| `POST` | `/projects/{id}/preview` | Execute saved selectors on the seed page |
| `POST` | `/projects/{id}/extract` | Start background same-site crawl/extraction |
| `GET` | `/projects/{id}/records-page` | Server-side paginated records (preferred) |
| `GET` | `/projects/{id}/records` | Legacy paginated records (compat) |
| `GET` | `/projects/{id}/export?format=csv|json|xlsx` | Streaming file download |
| `POST` | `/projects/{id}/cancel` | Cancel active project |
| `DELETE` | `/projects/{id}` | Delete terminal project |

### Future endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/projects/{id}/stream` | SSE live progress |
| `GET` | `/projects/{id}/pages` | Per-page crawl status and diagnostics |
| `POST` | `/projects/{id}/pages/{page_id}/retry` | Requeue a specific failed page |
| `GET / POST / DELETE` | `/sessions` | Saved domain sessions for authenticated crawling (Phase 5) |
| `GET / PUT` | `/users/me` | Profile management |

The legacy `/scrape` API is intentionally kept during the transition and is visually demoted in the frontend. It may be removed or replaced after the new extraction/crawl pipeline fully covers its use case.

---

## 7. Phased Roadmap

### Phase 0.5 — Foundation Reset (implemented)

**Goal:** Remove the credit system, add provider management, establish resource control config.

Changes:
- Migration: drop `credits_remaining`, `daily_credit_limit`, `credits_reset_at` from users
- Migration: drop `system_state` table
- Migration: drop partial unique index from migration 003; replace with count-based admission check
- Migration: create `provider_configs` table
- New service: `app/services/provider_service.py` — CRUD, API key Fernet encryption/decryption, LiteLLM call wrapper with the JSON parsing pipeline (Section 3.1), capability detection
- New endpoints: `/providers` CRUD + `/providers/{id}/test` + password-confirmed `/providers/{id}/reveal-key`
- Update `app/services/admission.py`: check `MAX_CONCURRENT_JOBS_PER_USER` (configurable), no credit gate
- Update `app/models/user.py`: remove credit fields, add `default_provider_id`
- Update `app/core/config.py`: remove credit settings, add `PROVIDER_KEY_ENCRYPTION_SECRET` and resource control settings
- Remove APScheduler credit reset job
- Update tests: remove credit test coverage, add provider CRUD + capability detection tests

**Startup validation:** Add a Pydantic validator on `PROVIDER_KEY_ENCRYPTION_SECRET` in `app/core/config.py` that calls `Fernet(key)` at import time. If the key is missing, malformed, or not a valid 32-byte url-safe base64 Fernet key, raise a `ValueError`:

```
PROVIDER_KEY_ENCRYPTION_SECRET is missing or invalid.
Generate one: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The app must fail on startup — not on first use when keys are already being stored.

**`.env.example` bootstrapping:** Create `.env.example` in Phase 0.5 with `PROVIDER_KEY_ENCRYPTION_SECRET` documented, the generation command shown, and the warning: *"Back up this value separately from your database. Losing it makes all stored provider API keys permanently unrecoverable."* Full Docker and infrastructure documentation stays in Phase 5; key documentation and `.env.example` start here.

---

### Phase 1 — Dual-Mode Analysis Engine (implemented)

**Goal:** Intelligent site analysis that supports both extraction modes and both user workflows (Guided and Fast).

What built:
- **URL validation service** (`app/services/url_validator.py`): SSRF prevention (blocks RFC 1918 ranges, loopback, link-local, multicast, reserved ranges, and metadata IPs), scheme validation, redirect-target validation
- **Robots.txt service** (`app/services/robots_service.py`): parse and respect `robots.txt`; TTL cache; default on fetch failure is conservative (`deny`) via `ROBOTS_FAILURE_POLICY`
- **Fetcher service** (`app/services/fetcher.py`): static (httpx) + optional browser (Playwright), auto-detect render mode by checking content sparsity, content hash, configurable max page size, Windows Uvicorn selector-loop browser handling
- **Analysis service** (`app/services/analyzer.py`): build DOM summary before sending to LLM (titles, headings, repeated patterns, schema.org JSON-LD — not full HTML); call LiteLLM with schema validation + retry; cache by `content_hash` in `analysis_cache` table; route to structured or content analysis schema based on `extraction_mode`
- **State machine with Fast Mode semantics**: `QUEUED → ANALYZING → ANALYSIS_READY | AWAITING_SETUP | FAILED | CANCELED`. `workflow_mode=FAST` can land in `ANALYSIS_READY` only when confidence is high and warnings are empty; otherwise analysis completes in `AWAITING_SETUP`.
- **New pipeline**: `app/services/job_executor.py`
- **New endpoints**: `POST /jobs` (accepts `extraction_mode`, `workflow_mode`, `render_mode`, optional provider), `GET /jobs/{id}`, `GET /jobs`, `POST /jobs/{id}/cancel`, `DELETE /jobs/{id}`
- `extraction_mode`, `workflow_mode`, and `render_mode` are set at job creation and do not change during Phase 1

**Structured analysis output schema:**
```json
{
  "page_type": "listing|detail|mixed|search|other",
  "repeated_item_selector": "CSS selector",
  "candidate_fields": [
    {"name": "price", "type": "number", "selector": ".price",
     "sample_values": ["$19.99"], "confidence": 0.95}
  ],
  "detail_link_selector": "a.item-link",
  "pagination_selector": ".next-page",
  "estimated_pages": 50,
  "warnings": ["Site may use JavaScript rendering"],
  "confidence": 0.87
}
```

**Content analysis output schema:**
```json
{
  "content_type": "article|documentation|blog|forum|product|other",
  "primary_content_selector": "main article",
  "estimated_pages": 120,
  "avg_content_length": 2400,
  "recommended_chunking": "by_heading",
  "metadata_fields": ["title", "author", "published_date"],
  "warnings": [],
  "confidence": 0.91
}
```

**The implemented demo moment:** Submit URL → analysis job runs → frontend shows suggested fields/content structure, confidence, warnings, fetch metadata, and advanced selectors hidden behind a toggle.

**Now implemented:** project review/setup, real selector preview, same-site crawl execution, persisted records, and CSV/JSON/XLSX export through `/projects`. **Not implemented yet:** visual field selection, selector repair, authenticated content, and durable multi-worker recovery.

---

### Phase 2 — Extraction Engine + Frontend Extraction Setup (implemented)

**Goal:** Extend the existing React frontend and backend into a working end-to-end extraction pipeline for both output modes.

What was built:
- **Project workflow tables**: `crawl_pages`, `extraction_specs`, `preview_results`, `extracted_records`, `exports`, and `analysis_cache`.
- **State machine**: AWAITING_SETUP / ANALYSIS_READY → PREVIEWING → PREVIEW_READY → DISCOVERING → EXTRACTING → EXPORTING → COMPLETED.
- **Preview endpoint**: `POST /projects/{id}/preview` fetches the seed page and executes saved selectors for real sample rows.
- **DOM Summary improvements** (`app/services/dom_summary.py`): repeated container samples, table samples, `data-*` attributes, repeated class limit 15, character cap 10,000.
- **CSS extraction service** (`app/services/extractor.py`): BeautifulSoup CSS selectors, repeated container grouping, type coercion, extraction warnings, raw data always preserved.
- **Content extraction path**: selected primary content text plus selected metadata fields, without per-page AI calls.
- **URL normalizer** (`app/services/url_normalizer.py`): deduplication, tracking-param stripping, same-origin filtering, optional glob patterns.
- **Background extraction executor** (`app/services/project_extraction.py`): same-site crawl, page-level state persistence, robots/SSRF reuse, per-page failure isolation, bounded by `page_limit`.
- **Exports**: streamed CSV, JSON, and XLSX.

**Frontend extension**:
- Project workspace field selection.
- Real selector preview against the seed page.
- Extraction controls with page limit and export format.
- Page-state progress counts.
- Results table and CSV/JSON/XLSX export buttons.

Remaining Phase 2-class gaps now move into Phase 3 quality/reliability work: concurrent workers, durable lease recovery, challenge classification, template routing, and selector repair.

**Per-page failure isolation is the core reliability invariant.** A 1000-page job with 10 blocked pages and 3 failures still delivers 987 pages of records.

---

### Phase 3 — Full UI + Visual Interaction (~3 weeks)

**Goal:** Rebuild the UI as a polished, differentiated product. Non-technical accessibility is a first-class design requirement.

Stack: React + Vite + TypeScript + Tailwind + TanStack Query + Zustand + shadcn/ui

**Non-technical mode (default):**
- CSS selectors are hidden. Default view shows field labels, sample values, and plain-language confidence hints ("High confidence — will extract reliably" vs "Low confidence — may miss some pages").
- Selectors are available in an "Advanced" toggle for technical users.
- Error messages describe problems in plain language without requiring CSS knowledge.

**Visual field selection:**
- The seed page is rendered in a sandboxed preview.
- User clicks on elements they want to extract. System generates CSS selectors from the DOM path of the clicked element automatically.
- User labels the field (e.g., "Product Name"). No CSS knowledge required.
- Technical users can override the generated selector in the Advanced view.
- Implementation: backend serves seed page HTML; frontend renders in sandboxed iframe, intercepts click events, constructs selectors using a CSS path generator library.

**Extraction sandbox:**
- Before committing to a full crawl, the user can click "Preview Extraction" to see what the current configuration would extract from the seed page.
- Calls `POST /projects/{id}/preview` and renders selector-extracted sample records.
- Non-technical users can verify "yes, that's the data I wanted" before running 500 pages.

**Fast Mode UI:**

- When analysis confidence passes the gate, the analysis results screen shows **"Extract Now"** prominently alongside "Customize". The confidence score and field count are visible so the user can make an informed choice.
- When confidence is below the gate, the screen shows **"Review First"** as the primary action, with a plain-language explanation of what the AI was uncertain about. "Extract Anyway" is available as a secondary option for technical users who want to proceed despite low confidence.
- New users are never defaulted silently into Fast Mode. They see a clear choice: **"Extract Now"** (high confidence) or **"Review First"** (uncertain). Guided review is always the safe fallback.

**Content mode UI:**
- Shows document cards instead of data rows.
- Preview shows extracted text chunks with metadata.
- Chunk configuration: choose chunking strategy (by heading / by paragraph / by token count) and target chunk size.

**Full screens:**
1. Auth (login, register)
2. Dashboard — job history, mode badges, status
3. Provider Settings — add/edit/delete, run capability test
4. New Job — URL input, mode selector, Fast Mode toggle
5. Analysis Results — Guided Mode field editor (visual selection + Advanced toggle) or Fast Mode confirmation
6. Progress — SSE-driven: pages discovered, records extracted, current URL, blocked count, `page_blocked` events surfaced in real time
7. Results — structured: table + export; content: document cards + export
8. Job detail — full history, field config used, blocked pages summary, re-run option

**Frontend auth pattern**: access token in memory (XSS prevention), refresh token in `localStorage`, axios interceptor handles 401 → refresh → retry.

---

### Phase 4 — Structural Normalization + RAG Export (~1.5 weeks)

**Goal:** Post-extraction data quality and RAG pipeline integration.

**Normalization (structural-only — this boundary is enforced by design, not just convention):**
- Parse ambiguous field values into structured forms: `"$19.99 USD"` → `{"value": 19.99, "currency": "USD", "raw": "$19.99 USD"}`
- Standardize date formats to ISO 8601
- Split compound fields (first + last name, address components)
- Normalize whitespace, trim strings, handle encoding
- AI may assist with parsing ambiguous formats — it must not rewrite, summarize, compress, or remove information
- `raw_data` is never modified. Idempotent and reversible.
- UI toggle: "Raw" / "Normalized". Users can verify every transformation against the original.

**RAG export targets:**
- Clean Markdown export (full page content, heading-preserved)
- Chunked JSONL: `{"text": "...", "metadata": {"source_url": "...", "title": "...", "chunk_index": 0, "job_id": "..."}}`
- Direct export adapters: Chroma (local file), Weaviate, Pinecone-compatible format
- Configurable chunking: by heading, by paragraph, by character/token limit

---

### Phase 5 — Authenticated Content + OSS Readiness (~3 weeks)

**Goal:** Support legitimate authenticated content workflows. Make the project cloneable and runnable by anyone.

**Authenticated content (human-in-the-loop model):**
- The platform detects access blocks and pauses. The user resolves them in their own browser. The platform resumes. No remote browser streaming, no automatic bypass.
- **Authorization declaration per job**: user selects `access_basis` before starting: `"owner"` / `"permitted"` / `"public"`. Stored on ExtractionSpec.
- **Session cookie management**: user pastes cookies from their own browser for a specific domain. Stored encrypted (Fernet, domain-scoped, user-scoped, revocable). Used on subsequent fetches. Never stored plaintext, never logged, never returned in API responses.
- **Challenge resume**: job pauses at `CHALLENGE_REQUIRED`; user resolves in own browser; clicks "Retry" in UI; job resumes with saved session.
- **New endpoints**: `GET / POST / DELETE /sessions`

**OSS hardening:**
- `docker-compose.yml` — one-command local setup (app + postgres + optional redis)
- `.env.example` — expanded with all Phase 1–4 settings (base version created in Phase 0.5)
- `README.md` rewrite — setup instructions, BYOK guide per provider, screenshots
- Structured JSON logging — **implemented** (stdlib `logging` + JSON formatter + `contextvars`; see `docs/learning/11_logging_observability.md`)
- Redis-backed rate limiting (fallback to in-memory)
- Email verification flow (`EMAIL_ENABLED=false` to disable)
- Scheduled export cleanup (delete exports older than N days)
- `GET /jobs/{id}/pages`, `POST /jobs/{id}/pages/{page_id}/retry`
- Export metadata fields: `_job_id`, `_source_url`, `_extracted_at`
- Full integration test coverage (separate `TEST_DATABASE_URL`)

---

### Phase 6 — Community + Advanced (~ongoing)

Deferred until Phase 5 ships and real user feedback exists.

- **Scheduled/cron jobs**: run extraction on a schedule (site change monitoring)
- **Multi-site jobs**: one job spanning multiple domains
- **Plugin/extension system**: custom extractors, normalizers, community site presets
- **Site presets**: community-contributed extraction configs for common sites
- **Webhook notifications**: POST to user URL on job completion
- **Advanced authenticated content**: login flow recording, complex multi-step authentication

**Explicit non-goals (permanent — will not be built):**
- Automatic CAPTCHA solving
- Browser fingerprint spoofing
- Proxy rotation for evasion
- Stealth browser patches to bypass bot detection
- Credential stuffing or login automation against third-party sites

---

## 8. Risks and Mitigation

### Risk 1: AI analysis quality
Wrong selectors or poor content detection leaves users stuck. Mitigation: show confidence scores; always allow manual override; visual field selection (Phase 3) as a non-AI fallback for selectors; selector repair (Phase 2) for drift; extraction preview sandbox lets users verify before full crawl.

### Risk 2: LiteLLM structured output reliability
Not all providers return valid JSON schema responses. Mitigation: capability detection at registration; JSON parsing pipeline with schema validation and retry; surface raw LLM response on persistent failure — never silently corrupt data.

### Risk 3: JavaScript-heavy sites
Playwright adds memory and crash complexity. Mitigation: static fetch first; detect sparse content and fall back to browser; pool Playwright contexts; document memory requirements for JS-heavy workloads.

### Risk 4: Anti-scraping measures
Sites use CAPTCHAs, bot detection, IP blocks. Mitigation: respect `robots.txt` by default; configurable crawl delay and User-Agent. Challenge strategy is **detect-and-pause, not detect-and-bypass**. Job degrades gracefully — extracted records remain available regardless of blocked pages. Automatic CAPTCHA solving is a permanent non-goal.

### Risk 5: Resource consumption on large jobs
A 10,000-page crawl can exhaust server resources. Mitigation: `MAX_PAGES_PER_JOB` default 500; `CRAWL_CONCURRENCY` default 3; `MIN_CRAWL_DELAY_MS` default 500ms; document celery/arq worker mode for high-volume deployments.

### Risk 6: API key security
Storing third-party API keys in the database is a security responsibility. Mitigation: Fernet encryption using `PROVIDER_KEY_ENCRYPTION_SECRET` (separate from `SECRET_KEY`); **never log API key material** — log only `provider_config_id`, provider name, and operation status; normal API responses never include keys; explicit key reveal requires password confirmation and must not cache the plaintext client-side; HTTPS required for non-localhost deployments; key backup and rotation documented explicitly.

### Risk 7: Key encryption loss
`PROVIDER_KEY_ENCRYPTION_SECRET` loss = all stored provider API keys permanently unrecoverable. Mitigation: startup validation crashes the app if key is invalid; warn in setup docs and `.env.example`; provide a key rotation management command; recommend storing this value in a password manager separate from the database backup.

### Risk 8: Visual field selection accuracy
Auto-generated CSS selectors from DOM path clicks may be overly specific (e.g., `div:nth-child(3) > span`) and break on other pages. Mitigation: always show the generated selector in the Advanced view; extraction preview lets user verify against the seed page; the system suggests simplifications (strip nth-child specificity where possible); Advanced users can edit freely.

### Risk 9: Non-technical users hitting complex edge cases
A non-technical user submitting a JavaScript-rendered site, a paginated SPA, or a site behind a login has no path forward if the default experience fails silently. Mitigation: surfacing errors in plain language; the `page_blocked` SSE event explains what happened in human terms; Fast Mode explicitly warns if analysis confidence is low; partial exports are always available.

### Risk 10: Open source sustainability
This is a learning/portfolio project, not a business. If it gains traction, a hosted cloud version using the same codebase is the natural monetization path. Keep the architecture clean enough that a hosted version is addable without forking.

---

## 9. What to Build First vs Defer

**Build first (Phases 0.5–2):** Foundation reset + analysis engine + full extraction pipeline + minimal working frontend. By end of Phase 2, both extraction modes work end-to-end and are accessible through a basic UI.

**Build second (Phase 3):** Full UI with visual interaction. The minimal Phase 2 frontend validates the product concept; Phase 3 makes it polished and differentiated. Start Phase 3 only after Phase 2 is working end-to-end.

**Defer:**
- Email verification (`is_verified=True` by default in dev)
- Structural normalization (useful but not blocking extraction)
- RAG export adapters (Phase 4 — useful but Phase 2 JSONL export is sufficient for early RAG use)
- Docker setup (Phase 5 — when API is stable)
- Authenticated content (Phase 5)
- Plugin system and community features (Phase 6)

---

## 10. Migration Path From Current Codebase

The migration is additive, not a rewrite.

1. **Phase 0.5**: Drop credit columns; drop `system_state`; drop partial unique index (003); add `provider_configs`. Two to three small migrations.
2. **Phase 1**: Add `analysis_cache` table; add `extraction_mode` enum to jobs; add task state enum values (`QUEUED`, `ANALYZING`, `AWAITING_SETUP`).
3. **Phase 2**: Add `crawl_pages` (with lease fields and required indexes), `extraction_specs`, `extracted_records` (with `content_blocks`), `exports`. Add remaining enum values. Add challenge page states.

The most invasive single change is removing the credit system from `admission.py` and `task_state.py` — approximately 40–50 lines removed. The auth system, health endpoints, rate limiting, watchdog pattern, and scheduling infrastructure are all unchanged.

---

## 11. Dependencies to Add

```
litellm>=1.0.0          # Provider abstraction layer
playwright>=1.40.0      # Browser rendering
cryptography>=42.0.0    # API key encryption (Fernet)
cssselect>=1.2.0        # CSS selector parsing and validation
lxml>=5.0.0             # HTML parsing for structured extraction
trafilatura>=1.8.0      # Content extraction for RAG/content mode
sse-starlette>=1.6.0    # SSE streaming endpoint
openpyxl>=3.1.0         # XLSX export format
aiofiles>=23.0.0        # Async file writes for exports
                        # Structured JSON logging: implemented with stdlib logging +
                        # JSON formatter + contextvars (no structlog dependency added)
```

All existing dependencies remain. No removals.

---

## 12. Immediate Next Steps (Phase 0.5, in order)

1. Write migrations: drop credit columns, drop `system_state`, drop partial unique index, add `provider_configs`
2. Update `app/models/user.py`: remove credit fields, add `default_provider_id`
3. Update `app/core/config.py`: remove credit settings, add `PROVIDER_KEY_ENCRYPTION_SECRET` + resource control settings; add Fernet startup validator
4. Create `.env.example` with `PROVIDER_KEY_ENCRYPTION_SECRET` documented
5. Implement `app/services/provider_service.py`: CRUD, Fernet encryption, LiteLLM call wrapper with JSON parsing pipeline (Section 3.1), capability detection
6. Update `app/services/admission.py`: count-based check against `MAX_CONCURRENT_JOBS_PER_USER`, no credit gate
7. Strip credit logic from `app/services/task_state.py`
8. Remove APScheduler credit reset job from `app/core/scheduler.py`
9. Add `/providers` endpoints + `/providers/{id}/test`
10. Update tests: remove credit coverage, add provider CRUD + encryption + capability detection tests

Phase 0.5 is the prerequisite. Nothing in Phase 1 makes sense until the credit system is gone and provider management exists.
