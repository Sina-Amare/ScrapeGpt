# ScrapGPT — Repository Audit Report

> **Purpose:** Findings from a full codebase + documentation audit.
> **Date:** 2026-06-02
> **Auditor:** Full read of every source file, schema, test, and documentation file.

---

## Part 1 — Repository Findings

### Project Purpose (verified against code)

ScrapGPT is a credit-gated async web scraping API. Users authenticate with JWT, spend one credit to trigger a pipeline that fetches a URL with httpx, extracts text with BeautifulSoup, and passes the content through an LLM layer (currently a stub that returns a mock dict after a 1-second sleep). The user polls for the result. The long-term goal is AI-assisted multi-page extraction using Google Gemini for selector suggestion and deterministic DOM parsing for the actual crawl.

**Implemented today:** Backend only. No frontend. No real LLM.

### Major Subsystems

| Subsystem | Status | Files |
|-----------|--------|-------|
| FastAPI app and middleware | ✅ Complete | `main.py`, `api/v1/router.py` |
| JWT authentication | ✅ Complete | `endpoints/auth.py`, `core/security.py` |
| User + credit model | ✅ Complete | `models/user.py` |
| ScrapeTask state machine | ✅ Complete | `models/scrape_task.py` |
| Admission gate | ✅ Complete | `services/admission.py` |
| Async pipeline orchestration | ✅ Complete | `services/task_executor.py`, `services/task_state.py` |
| httpx + BS4 scraper | ✅ Complete | `services/scraper.py` |
| LLM integration | ❌ Stub only | `services/llm_processor.py` |
| Daily credit reset | ✅ Complete | `core/scheduler.py` |
| Watchdog | ⚠️ Has NULL bug | `services/watchdog.py` |
| Health / readiness | ✅ Complete | `endpoints/health.py`, `services/readiness.py` |
| Rate limiting | ⚠️ Partially broken | `core/rate_limit.py`, `endpoints/scrape.py` |
| Database migrations | ⚠️ Enum drift | `alembic/versions/` |
| Tests | ⚠️ Narrow (health only) | `tests/` |

### Implementation State: What is in Each Category

**Fully implemented and correct:**
- App factory + lifespan management
- CORS middleware
- JWT auth (register / login / refresh token flow)
- Credit system schema and daily reset CAS logic
- ScrapeTask state machine (`VALID_TRANSITIONS`, `can_transition_to`, terminal check)
- Admission gate (credit check + one-active-task + DB IntegrityError fallback)
- Pipeline executor with always-finalize guarantee
- httpx + BeautifulSoup scraper with text extraction and truncation
- Atomic credit deduction at LLM phase transition
- Multi-instance-safe credit reset via system_state CAS
- Bounded readiness probe with sanitized output
- 13 passing tests for health/readiness

**Implemented but with known bugs:**
- `POST /scrape/start` — SlowAPI parameter collision (rate limiting will crash)
- `GET /tasks/current` — shadowed by `{task_id}` route (always 422)
- Watchdog — NULL `updated_at` causes fresh stuck tasks to be skipped
- JWT `int(payload.sub)` — will 500 on malformed token
- Rate limiting key function — always IP-based, never per-user

**Implemented but inconsistent / unconstrained:**
- `SCRAPE_CREDIT_COST` config declared but hardcoded to 1 in the actual deduction
- `LLM_TIMEOUT` config declared but no timeout wraps the stub call
- `MAX_CONCURRENT_JOBS` config declared but nothing enforces it

**Stub / placeholder:**
- `llm_processor.py:process_with_llm()` — sleeps 1s, returns `{"summary": "...", "word_count": N, "analysis": "This is a stub response."}`

**Dead code (exists but nothing uses it):**
- `app/schemas/scrape.py` — entire file unused (scrape endpoint defines inline schemas)
- `User.use_credit()`, `User.ensure_credits_reset()`, `User.has_credits` — deprecated, not called
- `deps.py:require_credits`, `deps.py:deduct_credit` — deprecated, not called
- `deps.py:get_optional_user` — defined, exported, no endpoint uses it
- `base.py:SoftDeleteMixin`, `IDMixin`, `TableNameMixin` — defined, no model uses them
- `security.py:decode_token` — unverified decode, never called in production
- `requirements.txt:requests` — listed but no code imports it

**Planned / not started:**
- Gemini AI integration
- React frontend
- Playwright browser rendering
- Multi-page crawling engine
- Page-level checkpointing
- Export layer (CSV/JSON/JSONL/XLSX)
- SSE progress stream
- URL validation + SSRF prevention
- `robots.txt` handling

---

## Part 2 — Documentation Audit

### `docs/architecture.md` — Accuracy: High ✅

**Accurate:**
- Three-layer diagram matches code exactly
- Domain model table for `User` fields is correct
- State machine diagram is correct
- Request flow for `POST /scrape/start` is accurate
- Admission gate description is accurate
- Credit deduction timing and rationale are accurate
- Scheduled jobs description is accurate
- Auth (JWT fields, expiry, get_current_user behavior) is accurate
- Rate limiting description acknowledges the `request.state.user` bug

**Inaccurate:**
- Pipeline orchestration section says the executor "Loads the task and verifies `task.user_id == user_id`". This is wrong. `execute_scrape_pipeline` only loads the task and checks it exists — it does **not** verify ownership. The ownership check is exclusively inside `transition_to_llm_processing` in `task_state.py`. Fixed in `architecture.md`.

**Outdated / minor gaps:**
- Says "watchdog runs at 60-second intervals" — correct, but the thresholds for each state (3/5/10 min) are only mentioned in STATUS.md, not here
- Doesn't mention the route shadowing bug in the scrape endpoint section
- Mentions `test_engine` with NullPool as a commented-out alternative — this is in the code, fine

**Missing:**
- No mention of the dead code in `deps.py` or `models/user.py`
- No mention of `app/schemas/scrape.py` existing but unused

---

### `docs/STATUS.md` — Accuracy: Very High ✅

**Accurate:** All 9 listed bugs are confirmed against the code. Line number references are correct:
- Line 64-69 for SlowAPI collision ✅
- Line 124 vs 153 for route shadowing ✅
- Line 44 for watchdog NULL-skip ✅
- Line 88 for JWT int() cast ✅

**Missing from STATUS.md (not critical, but notable):**
- `requests` library in requirements.txt is unused (minor)
- `httpx` listed twice in requirements.txt (minor)
- `app/schemas/scrape.py` is dead code
- Several deprecated functions in the codebase
- `SCRAPE_CREDIT_COST` is declared in config but hardcoded in the actual deduction

---

### `docs/plan/ROADMAP.md` — Accuracy: High ✅

**Accurate:** The current state section correctly describes what exists. Product vision, phased plan (0–6), test plan, and API shape are all internally consistent.

**Minor gap:** Mentions `GEMINI_MODEL_FAST` and `GEMINI_MODEL_REASONING` as planned config names — these are planning artifacts, not yet in config.py, which is correct (they're future planned additions).

---

### `docs/PROJECT_CONTEXT.md` — Accuracy: High ✅

This is the most comprehensive single-file overview. It is substantially accurate.

**One inaccuracy:** The file tree in this document shows `app/schemas/scrape.py` exists (correct) and implies it's used by the scrape endpoint. In reality, the scrape endpoint defines its own inline schemas (`StartScrapeRequest`, `TaskResponse`) and does not import from `app/schemas/scrape.py`. That file is dead code.

**Minor gaps:** Does not mention the deprecated functions in the codebase, the unused `requests` library, or the `httpx` duplication.

---

### `docs/learning/01_scrape_tasks_design.md` — Accuracy: High ✅

Content matches the current code. All design decisions (partial unique index, enum type, VALID_TRANSITIONS, ON DELETE CASCADE) are correctly described.

**One note worth flagging:** The document warns "Raw SQL updates won't trigger `onupdate`" — this is directly relevant to the watchdog NULL-skip bug. A reader who internalized this warning would have predicted the bug.

---

### `docs/learning/02_admission_and_credits.md` — Accuracy: High ✅

Accurate. The credit deduction code sample shown in the doc matches the actual implementation in `task_state.py`.

---

### `docs/learning/03_async_scrape_pipeline.md` — Accuracy: Medium ⚠️

**Outdated:** States "Timeout: 60 seconds, enforced by httpx" in the scraper section. The actual code uses `settings.SCRAPE_TIMEOUT` (default 30s, not 60s). This was the old hardcoded value before `doc 04` fixed it (the fix added the settings reference, but `doc 03` was not updated).

**Also outdated:** States `asyncio.sleep(1.0)` isn't mentioned in the pipeline description — minor since this is the stub.

**Everything else:** Correct. The always-finalize pattern, credit deduction atomicity, and concurrency analysis all match the code.

---

### `docs/learning/04_pipeline_fixes.md` — Accuracy: High ✅

Accurate description of what was fixed. The code samples in the doc match the current implementation.

---

### `docs/ops/health.md` — Accuracy: High ✅

Fully accurate. The probe steps (5 SQL queries), reason codes, timeout config, and debugging checklist all match `services/readiness.py`.

**One minor discrepancy:** The doc calls `/api/v1/health` a "Liveness" probe, but in the code that endpoint is labeled `health_check()` (returning `{"status": "healthy", ...}`) while the actual liveness probe is `/api/v1/health/live`. The health.md table says "Liveness → `/health`" but this doesn't match `/health/live`. The distinction matters for Kubernetes probe configuration.

---

### Risky Knowledge Gaps

1. **The watchdog NULL-skip** — `docs/learning/01` warns about raw SQL and `onupdate`, but doesn't connect this to the watchdog. A developer adding new intermediate states or touching the watchdog might not realize `updated_at` starts NULL.

2. **`app/schemas/scrape.py` looks active** — A developer seeing this file in the project might assume it's the source of truth for scrape DTOs and modify it, then be confused why the API isn't affected. There is no comment or documentation explaining it's unused.

3. **Deprecated methods in `User` model look callable** — `use_credit()` and `has_credits` look like the correct way to check/consume credits. A developer could call them in new code, which would bypass the atomic SQL update and create a race condition.

4. **`decode_token` bypasses signature verification** — This function in `security.py` is clearly marked "WARNING: This does not verify the token signature!" but its presence in the security module might tempt someone to use it in a feature under time pressure.

5. **`SCRAPE_CREDIT_COST` config creates a false assumption** — A developer who sees this setting might assume changing it would change the credit cost. It won't, because `task_state.py` hardcodes `- 1`.

6. **The `requests` import in requirements.txt** — A developer might import `requests` in new code because it's listed as a dependency, not realizing `httpx` is the project's async HTTP client of choice.

---

## Part 3 — Missing Context

The following information cannot be derived from the codebase alone. This section is written for a future LLM or human collaborator who needs to understand the full history.

---

### 1. Original Architecture Design

**Name:** Original design document / initial system sketch

**Why needed:** Migration 002 reveals the project originally used different state names: `PERMISSION_GRANTED`, `SCRAPED`, `LLM_ANALYZED`, `OUTPUT_GENERATION`, `FINALIZED`. These suggest a different pipeline was initially planned — one where after LLM analysis, there was a separate "output generation" phase before finalization. The current states (`SCRAPING`, `LLM_PROCESSING`, `COMPLETED`) are simpler. Understanding the abandoned design would clarify whether the original stages should be revisited for the multi-page roadmap.

**Questions it would answer:**
- What was the "output generation" phase intended to do?
- Why was `FINALIZED` used instead of `COMPLETED`?
- Was there a design doc for the original multi-phase pipeline?

**Priority:** Low (historical interest; doesn't affect current work)

---

### 2. Gemini Model Selection Rationale

**Name:** AI provider decision log

**Why needed:** The roadmap specifies Gemini specifically (free Google AI Studio key). There's no documented comparison against other options (OpenAI, Claude API, Mistral, local Ollama). This matters for Phase 2 implementation.

**Questions it would answer:**
- Was Gemini chosen purely for free-tier availability?
- Were other providers evaluated?
- Is the free-tier constraint hard (no-spend rule) or soft (prefer free, upgrade if needed)?
- What are the specific Gemini model IDs and rate limits available to this project today?

**Priority:** High — needed before Phase 2 implementation begins.

---

### 3. Why Single-Host First

**Name:** Deployment constraints document

**Why needed:** The architecture is explicitly "single-host for now" (scheduler in-process, BackgroundTasks instead of Celery, in-memory rate limiting). The reason given is "MVP simplicity," but the actual constraints (budget, infrastructure availability, team size, timeline) are not documented.

**Questions it would answer:**
- Is multi-host ever the target, or will this always be a local tool?
- Is there a trigger for migrating to a real job queue (e.g., "when we have X concurrent users")?

**Priority:** Medium — affects architectural decisions as the product scales.

---

### 4. Product Scope Decision: Local Tool vs Public SaaS

**Name:** Product scope and business model document

**Why needed:** The roadmap states "local-first MVP, not a public hosted SaaS." Several features that would be critical for SaaS (billing, tenant isolation, admin tooling, abuse handling, multi-tenancy) are explicitly out of scope. But the credit system and rate limiting architecture suggest SaaS patterns. Is this expected to become a SaaS eventually?

**Questions it would answer:**
- Is the plan to open-source this?
- Is monetization ever the goal?
- Why build a credit system for a local tool?

**Priority:** Medium — the credit system design is harder to justify without understanding the target user.

---

### 5. Previous Implementation Discussions

**Name:** Design conversations (Cursor/ChatGPT/Claude session logs)

**Why needed:** `docs/learning/04_pipeline_fixes.md` documents several bugs that were "fixed" — credit reset bypass, transaction bugs, hardcoded timeouts. This implies earlier implementation sessions where these bugs were introduced and then corrected. Those sessions likely contain design reasoning that wasn't captured in docs.

**Questions it would answer:**
- Was the lazy credit reset approach tried and then abandoned, or never really implemented?
- Was there a phase where admissions deducted credits (later moved to LLM phase)?
- What drove the specific watchdog timeout values (3/5/10 min)?

**Priority:** Low — the current docs capture most of the relevant rationale.

---

### 6. Frontend Stack Decision Log

**Name:** Frontend framework evaluation

**Why needed:** The roadmap specifies React + Vite + TypeScript + Tailwind + shadcn/ui + TanStack Query. This specific stack was chosen but there is no documented rationale.

**Questions it would answer:**
- Why TanStack Query over React Query (they're the same) or SWR or Zustand?
- Why shadcn/ui specifically?
- Was Next.js or any SSR framework considered?
- Is the frontend expected to be in the same repo or a separate one?

**Priority:** Low — frontend hasn't started yet; easy to revisit when the time comes.

---

### 7. Data Retention Policy

**Name:** Task history policy

**Why needed:** `ScrapeTask` records are never deleted in the current implementation. Task history accumulates indefinitely. The `ON DELETE CASCADE` on `user_id` only removes tasks when a user is deleted. There is no cleanup for old terminal tasks.

**Questions it would answer:**
- Should completed tasks be deleted after N days?
- Should `content` (raw scraped text) be deleted after the task is processed?
- Are there storage/privacy concerns with retaining scraped content?

**Priority:** Medium — relevant before any production deployment.

---

## Part 4 — Recommended Documentation Structure

The current documentation structure is good but has some redundancy (PROJECT_CONTEXT.md and architecture.md cover similar ground) and gaps (no developer setup doc, no testing guide, no explicit entry point for a first-time reader).

### Current structure

```
docs/
├── README.md                 ← Index only
├── PROJECT_CONTEXT.md        ← Comprehensive LLM handoff doc
├── architecture.md           ← Architecture + design decisions
├── STATUS.md                 ← Bug list + what's next
├── plan/
│   └── ROADMAP.md            ← Full product roadmap
├── ops/
│   └── health.md             ← Health endpoint operator guide
└── learning/
    ├── 01_scrape_tasks_design.md
    ├── 02_admission_and_credits.md
    ├── 03_async_scrape_pipeline.md
    └── 04_pipeline_fixes.md
```

### Recommended structure

```
docs/
├── implementation.md    ← NEW: Canonical entry point (created this audit)
├── audit_report.md      ← NEW: This document
├── README.md            ← Update: point to implementation.md as the entry point
│
├── reference/                           ← Rename from flat files
│   ├── architecture.md                  ← Keep (authoritative design doc)
│   ├── STATUS.md                        ← Keep (living bug/status tracker)
│   └── PROJECT_CONTEXT.md              ← Keep (LLM handoff context)
│
├── plan/
│   └── ROADMAP.md                       ← Keep unchanged
│
├── ops/
│   └── health.md                        ← Keep unchanged
│
└── learning/                            ← Keep, continue adding
    ├── 01_scrape_tasks_design.md
    ├── 02_admission_and_credits.md
    ├── 03_async_scrape_pipeline.md  ← Update scraper timeout (60s → settings.SCRAPE_TIMEOUT)
    ├── 04_pipeline_fixes.md
    └── 05_*.md                      ← Next task
```

### Why each document should exist

| Document | Why |
|----------|-----|
| `implementation.md` | Single document a new engineer reads first. Maps the whole system. Verifiable against code. |
| `audit_report.md` | Captures what was found in this audit. Documents gaps, dead code, and missing context requests. Prevents future developers from rediscovering the same issues. |
| `reference/architecture.md` | "Why was it built this way?" architecture reasoning. Complements `implementation.md`. |
| `reference/STATUS.md` | Living document. Tracks known bugs and current phase. Must stay up to date. |
| `reference/PROJECT_CONTEXT.md` | Fast LLM onboarding. Summarizes everything without being exhaustive. Useful for short-context LLM sessions. |
| `plan/ROADMAP.md` | Product and implementation plan. Source of truth for what to build next. |
| `ops/health.md` | Operator-facing. Explains health endpoint contracts, reason codes, debugging. |
| `learning/*.md` | Decision logs. Mandatory per `.agent/rules/documenting.md`. Capture *why* after each implementation task. |
