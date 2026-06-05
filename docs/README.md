# docs/

| File | Purpose |
|------|---------|
| [project_master.md](project_master.md) | **Start here.** Current state, architecture, setup, testing, and the full phase-by-phase implementation roadmap. |
| [ops/health.md](ops/health.md) | Operator guide for `/health/ready` — probe steps, reason codes, debugging. |
| [learning/01_scrape_tasks_design.md](learning/01_scrape_tasks_design.md) | Why: partial unique index, state machine design, concurrency safety. |
| [learning/02_admission_and_credits.md](learning/02_admission_and_credits.md) | Why: credit deduction at LLM phase, not at admission. |
| [learning/03_async_scrape_pipeline.md](learning/03_async_scrape_pipeline.md) | Why: always-finalize guarantee, background task pattern, watchdog. |
| [learning/04_pipeline_fixes.md](learning/04_pipeline_fixes.md) | Why: credit reset CAS, transaction isolation, ownership validation. |
| [learning/05_phase0_security_fixes.md](learning/05_phase0_security_fixes.md) | Why: rate-limit key uses verify_token, refresh endpoint rate-limited. |

`learning/` — decision logs explaining *why* things are built the way they are. Add one after every non-trivial implementation task.
