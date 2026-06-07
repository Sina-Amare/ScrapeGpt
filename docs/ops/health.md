# Health Endpoints

## Endpoint Overview

There are three endpoints. Use the right one for the right purpose:

| Probe          | Endpoint                    | Purpose                                                             |
| -------------- | --------------------------- | ------------------------------------------------------------------- |
| **Liveness**   | `GET /api/v1/health/live`   | Confirms the process is running. Returns `{"alive": true}`.         |
| **Readiness**  | `GET /api/v1/health/ready`  | Confirms the instance can serve traffic (DB up, schema compatible). |
| **Basic info** | `GET /api/v1/health`        | Human-readable status (env, version) for casual checks.             |

> **Note:** Use `/health/live` for Kubernetes liveness probes, **not** `/health`. The `/health` endpoint does not check any dependencies.

---

## `/health/ready` Contract

### Success — `200 OK`

```json
{
  "status": "ready",
  "environment": "development",
  "version": "0.1.0",
  "database": "ok"
}
```

### Failure — `503 Service Unavailable`

```json
{
  "status": "not_ready",
  "environment": "development",
  "version": "0.1.0",
  "database": "<reason_code>"
}
```

---

## Reason Codes

| Code                  | Meaning                                    | Likely Cause                           |
| --------------------- | ------------------------------------------ | -------------------------------------- |
| `ok`                  | All probes passed                          | —                                      |
| `db_unreachable`      | Cannot connect to PostgreSQL               | Network / credentials / PG down        |
| `schema_incompatible` | Missing table, column, or Alembic revision | Migrations not applied                 |
| `query_failed`        | Connected but query errored                | Permission issue / unexpected DB error |
| `timeout`             | Probes did not finish in time              | Slow network / overloaded DB           |

---

## Timeout Configuration

| Setting                     | Default | Range        | Env Var                     |
| --------------------------- | ------- | ------------ | --------------------------- |
| `READINESS_TIMEOUT_SECONDS` | `2.0`   | `0.5 – 10.0` | `READINESS_TIMEOUT_SECONDS` |

All probes run inside `asyncio.wait_for`, so the endpoint is guaranteed to return
within `READINESS_TIMEOUT_SECONDS` regardless of DB latency.

---

## What the Probes Check

1. **Connectivity** — `SELECT 1`
2. **Migration state** — `SELECT version_num FROM alembic_version LIMIT 1` (must return a row)
3. **Schema sanity** — column-level probes on:
   - `users`
   - `scrape_tasks`
   - `provider_configs`
   - `projects`
   - `extraction_specs`
   - `preview_results`
   - `crawl_pages`
   - `extracted_records`
   - `exports`
   - `analysis_cache`

---

## Debugging Checklist

1. **`db_unreachable`**
   - Verify `DATABASE_URL` is correct and the host is reachable.
   - Check PostgreSQL is running: `pg_isready -h <host> -p <port>`.
   - Confirm firewall / security-group rules allow the connection.

2. **`schema_incompatible`**
   - Run pending migrations: `alembic upgrade head`.
   - Confirm the `alembic_version` table exists and has a row.
   - Verify required tables (`users`, `scrape_tasks`, `provider_configs`, `projects`, `extraction_specs`, `preview_results`, `crawl_pages`, `extracted_records`, `exports`, `analysis_cache`) and their columns.

3. **`query_failed`**
   - Check DB user permissions (`SELECT` on all required tables).
   - Look at application logs for the `readiness.check_failed` entry (includes `error_type`).

4. **`timeout`**
   - Increase `READINESS_TIMEOUT_SECONDS` if DB latency is expected.
   - Check DB connection-pool exhaustion (`max_overflow`, `pool_size` in config).
   - Monitor DB query performance.

---

## Security

- Raw exception messages and stack traces are **never** exposed in the response.
- Only controlled `ReadinessCode` literals appear in the `database` field.
- Exception class names are logged server-side only (`readiness.check_failed`).
