# Documentation Index

## Start Here

| Document                               | Purpose                                                                                     |
| -------------------------------------- | ------------------------------------------------------------------------------------------- |
| [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) | Handoff context for a new LLM or human session: what exists, what is planned, and key rules. |
| [plan/ROADMAP.md](plan/ROADMAP.md)     | Unified product and implementation roadmap. This replaces the old separate plan drafts.      |
| [STATUS.md](STATUS.md)                 | Known bugs and unfinished work in the current codebase.                                     |
| [architecture.md](architecture.md)     | How the current backend is structured: layers, state machine, data model, design decisions. |
| [ops/health.md](ops/health.md)         | Operator guide for health/readiness endpoints.                                              |

## Decision Logs

These document _why_ things were built the way they are. Read them to understand the reasoning behind the architecture.

| Document                                                                     | Topic                                                           |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------- |
| [learning/01_scrape_tasks_design.md](learning/01_scrape_tasks_design.md)     | Partial unique index, state machine, concurrency safety         |
| [learning/02_admission_and_credits.md](learning/02_admission_and_credits.md) | Admission gate, credit deduction timing, atomicity              |
| [learning/03_async_scrape_pipeline.md](learning/03_async_scrape_pipeline.md) | Background pipeline, always-finalize, watchdog                  |
| [learning/04_pipeline_fixes.md](learning/04_pipeline_fixes.md)               | Credit reset scheduler, transaction fixes, ownership validation |
