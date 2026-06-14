# ScrapeGPT — Testing Guide

A **bounded, prioritized** checklist for testing the scraping pipeline and the
application end to end. It is ordered by value: do the tiers top‑down and **stop
when Tier 0–3 pass** — that is the definition of "the pipeline works." Tiers 4–6
are for deeper/edge confidence and only need re‑running when you touch those areas.

> This is not "scrape everything forever." Each case below names **one**
> representative real URL and the **exact expected result**. When a tier's cases
> pass, move on — don't keep adding sites.

---

## 0. Setup (once)

```powershell
# Backend deps (MUST include brotli/zstd — many CDNs serve those encodings)
pip install -r requirements.txt
# Optional stealth backends (only needed for the anti-bot / JS tests in Tier 2)
pip install playwright; python -m playwright install chromium
pip install playwright-stealth
pip install "camoufox[geoip]"; python -m camoufox fetch

# DB schema
alembic upgrade head

# Frontend deps
cd frontend; npm install; cd ..

# Run both servers in the background
.\dev-start.ps1     # backend 127.0.0.1:8000, frontend 127.0.0.1:5173
```

Prerequisites for end‑to‑end (E2E) tests:

- A registered user (Tier 4 covers creating one).
- **At least one working BYOK provider key** (e.g. a free Gemini key from Google
  AI Studio). Add it in **Providers**. Without it, analysis fails with
  `NO_PROVIDER_CONFIGURED` — which is itself a valid Tier 3 test.

Sanity check the deps before anything else:

```powershell
venv\Scripts\python.exe -c "from httpx._decoders import SUPPORTED_DECODERS; print(list(SUPPORTED_DECODERS))"
# Must include 'br' and 'zstd'. If not, the decode fix is inactive — reinstall requirements.
```

---

## 1. Automated tests (run first — fast, no provider key needed)

These must be green before any manual testing. They are the regression net.

```powershell
# Backend — full suite (~15s)
venv\Scripts\python.exe -m pytest -q
# Expected: ~487 passed, ~10 skipped

# Frontend
cd frontend
npm.cmd test          # ~70 passed
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
cd ..
```

What the automated suite already covers (you do **not** need to manually re‑test
these): URL/SSRF validation, brotli/zstd + charset decode, `assess_html_quality`,
the deterministic table fallback + column matching, the extract gates, frontier
scope classification, provider key encryption, auth tokens, watchdog/lease reaper,
state‑machine transitions. Focused files worth knowing:
`tests/services/test_fetcher.py`, `test_extractor.py`, `test_html_quality.py`,
`test_frontier_preview.py`, `test_reliability_hardening.py`,
`tests/api/v1/test_projects*.py`.

Optional scripted E2E harness (8 API scenarios, no UI):

```powershell
venv\Scripts\python.exe tests\validation\run_validation.py   # expect 8/8 PASSED
```

---

## 2. Real‑URL test targets (curated, stable, ethical)

All are public **scraping sandboxes** or scrape‑tolerant references. The
"Encoding" column matters for the decode tests (verified live):

| # | URL | Shape | Encoding | Good for |
|---|-----|-------|----------|----------|
| A | `https://www.calories.info/food/beef-veal` | Single big table | **zstd** | Structured, table fallback, decode |
| B | `https://books.toscrape.com/` | Listing + pagination + detail | **br** | PAGINATION, DATASET, decode |
| C | `https://quotes.toscrape.com/` | Listing + pagination + author detail | **br** | PAGINATION, DATASET |
| D | `https://quotes.toscrape.com/js/` | JS‑rendered listing | br | BROWSER render / sparse fallback |
| E | `https://www.scrapethissite.com/pages/simple/` | Country cards | **zstd** | Structured, CURRENT_PAGE |
| F | `https://www.scrapethissite.com/pages/forms/` | Paginated hockey table | zstd | PAGINATION + table |
| G | `https://webscraper.io/test-sites/e-commerce/allinone` | Product grid | gzip | Structured, DATASET |
| H | `https://en.wikipedia.org/wiki/List_of_countries_and_dependencies_by_population` | Large reference table | gzip | Structured table, large export |
| I | `https://news.ycombinator.com/` | Repeated rows, no obvious table | gzip | Repeated‑container vs table |
| J | A long article / docs page (e.g. any Wikipedia article) | Prose | — | CONTENT mode |

SSRF / negative targets (must be **rejected**, never fetched):
`http://127.0.0.1:8000/`, `http://localhost/`, `http://169.254.169.254/latest/meta-data/`,
`http://192.168.0.1/`, a `*.pdf` URL, a guaranteed 404.

> Be a good citizen: keep the **Safety limit** small (5–20 pages) for crawl tests.
> The toscrape/scrapethissite/webscraper.io sites exist for this; don't hammer them.

---

## TIER 0 — Smoke test (the one path that must work)

If this passes, the core pipeline is alive. ~5 minutes.

1. Log in; confirm a provider is configured (Providers shows it; **Test** passes).
2. **New Extraction** → URL **A** (`calories.info/food/beef-veal`), mode
   **"Rows in a table"** → Analyze.
3. Project reaches **Analysis ready** (not Failed). Open the workspace.
4. **Crawl scope** → "This page only" (CURRENT_PAGE) — no confirmation needed.
5. **Fields** → at least Food Name + Calories selected → Save.
6. **Preview** → returns sample rows (not "no records").
7. **Extract** → completes → **Results** shows ~40+ rows with sane values.
8. **Export** → download **CSV**, open it → columns + rows present, no mojibake.

**Pass criteria:** real rows extracted and exported from a zstd‑compressed page.
This single test proves the decode fix + extraction + export. If it fails, stop
and diagnose before going further.

---

## TIER 1 — Core functional matrix

One pass each. Don't repeat across many sites — one representative URL per row.

| Case | URL | Steps | Expected |
|------|-----|-------|----------|
| Structured / CURRENT_PAGE | E | analyze (table mode) → fields → preview → extract | rows from the single page |
| Content / RAG mode | J | analyze as **"Content for knowledge base"** → preview → extract | cleaned primary text + selected metadata, not tabular noise |
| PAGINATION scope | C or F | scope = "Paginated list" → **confirm scope** → frontier preview → extract (limit ~5) | crawls page 1..N; records from multiple pages |
| DATASET scope | B | scope = "Listing + detail pages" → confirm → frontier preview → extract (limit ~10) | listing + per‑item detail pages crawled |
| FULL_SITE scope | B | scope = "Entire website" → **broad‑scope warning shown** → confirm → frontier preview | many same‑origin URLs included; warning visible |
| Export CSV | any completed | Results → Export → CSV | opens cleanly, spec field order, source_url last |
| Export JSON | any completed | Export → JSON | valid JSON array of records |
| Export XLSX | any completed | Export → XLSX | opens in Excel, styled header row |
| Paginated results table | H (large) | extract → Results | server‑side paging (50/100/250/500), counts correct |

**Scope confirmation gate:** for any non‑CURRENT_PAGE scope, try **Extract before
confirming** → must be blocked with `SCOPE_NOT_CONFIRMED` (UI: "Confirm what
ScrapeGPT should crawl"). Confirm, then extract proceeds.

---

## TIER 2 — Pipeline robustness (the hardening)

| Case | URL / How | Expected |
|------|-----------|----------|
| **Brotli decode** | B or C (br) | analyzes to clean HTML; **no** "corrupted/binary data" warning; preview finds rows |
| **Zstd decode** | A or E (zstd) | same — clean analysis, rows found |
| **gzip decode** | G or H | clean analysis |
| **Table fallback** (weak AI selectors) | A | even if the AI's container selector is wrong, extraction still returns table rows (the deterministic fallback). Verify via the script below |
| **Repeated‑container vs table** | I (HN) | rows extracted from repeated structure (no `<table>`) |
| **JS‑rendered / sparse → browser** | D | with render mode **AUTO** and Playwright/camoufox installed, sparse static HTML triggers the stealth browser and content appears; **without** browser backends, expect a clean `FETCH_HTML_QUALITY_FAILED`, not garbage |
| **Browser render mode** | D | set render mode **BROWSER**; content extracted via headless browser |
| **Anti‑bot / Cloudflare** | any CF‑protected site you know | challenge detected → if JS challenge + browser available, retried; interactive Turnstile/CAPTCHA → fails cleanly as `BOT_PROTECTION_BLOCKED` with a guidance message (never silently "0 records"). *Don't pin a fragile CF URL into a permanent test — verify once.* |

Quick scripted proof of the **decode + table fallback** without the UI:

```powershell
venv\Scripts\python.exe -c "import asyncio; from app.services.fetcher import fetch_url; r=asyncio.run(fetch_url('https://books.toscrape.com/','STATIC')); print(len(r.html), 'Books to Scrape' in r.html, '�' in r.html)"
# Expect: large length, True, False  (br decoded cleanly)
```

---

## TIER 3 — Error & edge cases (must fail *loudly and correctly*)

These verify the pipeline never "succeeds with nothing." Check the **error_code**
(visible in the project Overview error + the activity log; use **Show raw debug
data** for details).

| Case | How to trigger | Expected error_code |
|------|----------------|---------------------|
| No provider | remove all providers, then analyze | `NO_PROVIDER_CONFIGURED` |
| Active‑job limit | start more than `MAX_CONCURRENT_JOBS_PER_USER` (default 3) | `ACTIVE_JOB_LIMIT_REACHED` |
| SSRF — loopback | analyze `http://127.0.0.1:8000/` | `URL_BLOCKED` (rejected immediately) |
| SSRF — metadata IP | analyze `http://169.254.169.254/latest/meta-data/` | `URL_BLOCKED` |
| Non‑HTML | analyze a `*.pdf` URL | `UNSUPPORTED_CONTENT_TYPE` |
| Dead URL | analyze a known 404 | fetch failure (`FETCH_FAILED`/4xx surfaced), project FAILED |
| Undecodable page | (covered automatically) a body we can't decode | `PAGE_DECODE_FAILED` — not garbage to the LLM |
| Zero records | structured extract where selectors match nothing (e.g. set deliberately wrong fields on J) | project FAILED `NO_RECORDS_EXTRACTED`; the page shows under **"page(s) failed"** with reason "Selectors matched no elements", **not** as Extracted |
| All pages failed | crawl where every page errors/blocks | `ALL_PAGES_FAILED` (or `BOT_PROTECTION_BLOCKED` if all anti‑bot) |
| No preview | extract before previewing (no `extract_anyway`) | 409 `NO_PREVIEW`; "Extract anyway" offered |
| Stale preview | change fields after a preview, then extract | 409 `STALE_PREVIEW`; "Extract anyway" offered |
| **Zero‑record preview (hard gate)** | preview returns 0 rows, then click extract | 409 `ZERO_PREVIEW_RECORDS`; **"Extract anyway" is NOT offered** — only "Adjust fields" |
| Scope mismatch | PAGINATION scope on a page with detail links but no pagination | frontier preview shows `SCOPE_NO_MATCHING_LINKS` recommending DATASET |
| Cancel | start a crawl, hit Cancel | project → CANCELED; crawl stops |
| Retry | on a FAILED project, Retry (optionally new provider) | reopens from field setup (analysis kept) or re‑analyzes |

---

## TIER 4 — Auth & security

| Case | Expected |
|------|----------|
| Register / login / logout | tokens issued; access token in memory, refresh in localStorage |
| Token refresh on 401 | expired access token silently refreshes; session continues |
| Password reset | request code (emailed if SMTP set, else dev‑logged) → confirm → old tokens rejected |
| Provider key never returned | GET providers never includes key material |
| Key reveal | requires password re‑confirm; wrong password → 401; success logs `security.key_revealed` |
| Ownership isolation | access another user's project/provider id → **404** (not 403; existence not revealed) |
| Rate limits | hammer `/auth/login` or `/providers/{id}/reveal-key` | 429 after the limit (auth 5/min, scrape 10/min) |

---

## TIER 5 — Reliability & background

Mostly covered by the automated suite; spot‑check live only if you changed these.

| Case | How | Expected |
|------|-----|----------|
| Crash recovery | kill the backend mid‑extraction, restart | watchdog fails the stuck project after the EXTRACTING timeout (default 10 min); not stuck forever |
| Lease reaper | (unit‑tested) | FETCHING pages with expired leases reset to PENDING within 60s, active projects only |
| Analysis cache | analyze the same URL twice | 2nd is a cache hit (`analyzer.cache_hit` in logs), faster, no 2nd LLM call |
| Cache not poisoned | a binary/garbage fetch | analysis is **not** cached (`analyzer.cache_skipped_binary_summary`) |
| Export cap | extract a large set (H) then export | all rows exported in chunks; warning logged if >10k |

Watch structured logs while testing (`.dev-backend.log` or stdout): look for
`http.request`, `analyzer.*`, `extraction.*`, `frontier.*`, `fetcher.*`,
`watchdog.*`. Confirm **no secrets** (keys, tokens, passwords, record content) ever appear.

---

## TIER 6 — Frontend UX

| Case | Expected |
|------|----------|
| Provider dropdown (long model name) | trigger truncates inside the box; menu not clipped; scrolls; flips up near viewport bottom |
| Select an option | selects and closes (does **not** instantly reopen) |
| Scope selector | shows AI‑suggested mode; broad modes (DATASET/FULL_SITE) require explicit confirm; status AI_SUGGESTED → USER_CONFIRMED |
| Frontier preview panel | included/excluded URL samples with reason codes; warnings (e.g. scope mismatch) shown |
| Trust summary panel | after extraction: per‑field success rates, missing rates, overall good/needs_review/risky |
| Provider‑test toast across logout | start a provider test, log out immediately → **no** stale toast on the login screen; log back in → previous result doesn't reappear |
| Activity log | dashboard + project events show analysis/extraction milestones |

---

## Definition of done (stop here)

You can consider the pipeline **verified** when:

- Tier 1 automated tests are green (backend + frontend).
- Tier 0 smoke test passes end‑to‑end on a real (zstd) site.
- Tier 1 matrix passes once per row.
- Tier 2 decode cases (br / zstd / gzip) all analyze cleanly, and the table
  fallback returns rows on URL A.
- Tier 3 error cases each produce the **correct error_code** (no silent
  "success with zero records", no garbage to the LLM).

Tiers 4–6 are area‑specific; re‑run only the relevant tier when you change auth,
background jobs, or the frontend. **Do not** expand into testing dozens of extra
sites — the matrix above is representative by design.

---

## Quick reference

**Error codes you should see (and when):**

- Analysis: `NO_PROVIDER_CONFIGURED`, `ACTIVE_JOB_LIMIT_REACHED`, `ANALYSIS_FAILED`,
  `PAGE_DECODE_FAILED`, `FETCH_HTML_QUALITY_FAILED`, `URL_BLOCKED`,
  `UNSUPPORTED_CONTENT_TYPE`, `FETCH_TIMEOUT`, `FETCH_FAILED`, `TOO_MANY_REDIRECTS`
- Extract gates: `SCOPE_NOT_CONFIRMED`, `NO_PREVIEW`, `STALE_PREVIEW`, `ZERO_PREVIEW_RECORDS`
- Extraction outcome: `NO_RECORDS_EXTRACTED`, `ALL_PAGES_FAILED`, `BOT_PROTECTION_BLOCKED`
- Per‑page `block_reason`: `ANTI_BOT_CHALLENGE`, `PAGE_DECODE_FAILED`, `SELECTOR_ZERO_MATCH`
- Frontier warnings: `SCOPE_NO_MATCHING_LINKS`, `FRONTIER_HAS_MANY_EXCLUSIONS`, `SEED_FETCH_FAILED`

**Notes / non‑goals (don't test these as bugs):**

- **robots.txt is not enforced** in the fetch pipelines (enforcement was removed) —
  do not expect robots‑based blocking.
- Interactive CAPTCHA / Turnstile solving is a **permanent non‑goal** — detection +
  clean failure is the correct behavior, not a bypass.
- Concurrent crawl workers are not implemented (single sequential executor);
  `CRAWL_CONCURRENCY` is reserved.
- The legacy **`/scrape`** page still exists but is not the primary flow; test it
  only if you specifically rely on it.

**Handy commands:**

```powershell
venv\Scripts\python.exe -m pytest -q                       # backend tests
cd frontend; npm.cmd test; npm.cmd run typecheck; npm.cmd run lint; npm.cmd run build; cd ..
venv\Scripts\python.exe tests\validation\run_validation.py # scripted E2E
.\dev-start.ps1 ; .\dev-stop.ps1                            # run / stop servers
```
