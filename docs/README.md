# ScrapeGPT Docs

## Start Here

These two files describe what the system does right now:

- [STATUS.md](STATUS.md) — implemented features, not-yet-implemented items, known issues, and last verified test results. Read this first.
- [product/strategic_redesign.md](product/strategic_redesign.md) — active forward roadmap (Phases 3–6) and architectural decisions. Read this second.

---

## Operations

- [ops/health.md](ops/health.md) — operator guide for `/health/ready`: probe steps, reason codes, and debugging checklist.

---

## Implementation Decision Logs (`learning/`)

Added after every non-trivial implementation task. Explain the *why*, not just the *what*.
Read the ones most relevant to the code you are working on.

| # | Document | Covers |
|---|---|---|
| 01 | [learning/01_phase0_security_fixes.md](learning/01_phase0_security_fixes.md) | JWT signature verification in rate-limit key; refresh endpoint rate limiting. |
| 02 | [learning/02_phase_0_5_provider_foundation.md](learning/02_phase_0_5_provider_foundation.md) | BYOK provider model, credit removal, Fernet-encrypted keys. |
| 03 | [learning/03_showcase_frontend_phase05.md](learning/03_showcase_frontend_phase05.md) | First React frontend shell and provider/task UX. |
| 04 | [learning/04_polish_and_tests.md](learning/04_polish_and_tests.md) | Frontend polish and test hardening before Phase 1. |
| 05 | [learning/05_task_deletion_and_results_view.md](learning/05_task_deletion_and_results_view.md) | Task result viewing and terminal-task deletion. |
| 06 | [learning/06_phase1_analysis_jobs.md](learning/06_phase1_analysis_jobs.md) | Phase 1 analysis jobs: URL safety, robots, fetcher, DOM summary, cached LLM analysis, frontend jobs UI. |
| 07 | [learning/07_frontend_robustness_and_polish.md](learning/07_frontend_robustness_and_polish.md) | Final Phase 1 UX polish and browser error handling. |
| 08 | [learning/08_project_workflow_migration.md](learning/08_project_workflow_migration.md) | Project workflow migration: Analyze → Fields → Preview → Extract → Results. |
| 09 | [learning/09_phase2_real_extraction_engine.md](learning/09_phase2_real_extraction_engine.md) | Real selector preview, same-site crawling, persisted records, and CSV/JSON/XLSX export. |
| 10 | [learning/10_phase25_scope_frontier_trust.md](learning/10_phase25_scope_frontier_trust.md) | Crawl scope, frontier preview, scope confirmation, trust signals, paginated results, and validation. |
| 11 | [learning/11_logging_observability.md](learning/11_logging_observability.md) | Structured logging with stdlib + contextvars: architecture, invariants, event catalog, security guarantees. |
| 12 | [learning/12_reliability_hardening.md](learning/12_reliability_hardening.md) | Phase 2.5 closeout: legacy scrape SSRF (all levels), CrawlPage lease reaper, stuck-project watchdog, all-pages-failed semantics. |

---

## Reviews and Validation (`reviews/`)

Point-in-time audits and validation reports. Each includes a resolution note where findings have since been addressed.

- [reviews/01_codebase_audit.md](reviews/01_codebase_audit.md) — Code-first project audit (June 9, 2026). Critical findings resolved in the Phase 2.5 hardening pass; resolution table at the top.
- [reviews/02_product_ux_strategy.md](reviews/02_product_ux_strategy.md) — Product UX and architecture strategy review. SSRF and lease-reaper items marked resolved.
- [reviews/03_phase25_validation.md](reviews/03_phase25_validation.md) — Post-Phase 2.5 E2E validation: 8/8 scenarios passed.

---

## Archive (`archive/`)

Historical material, preserved for context but not authoritative for current development.

- [archive/project_master.md](archive/project_master.md) — **ARCHIVED.** Pre-redesign reference (Phase 0). Describes the original credit-gated single-task system. Do not use for current development.
- [archive/scraping_capabilities_fa.md](archive/scraping_capabilities_fa.md) — **ARCHIVED.** Phase 2 scraping capabilities document written in Persian. Superseded by `STATUS.md` and `strategic_redesign.md`.
