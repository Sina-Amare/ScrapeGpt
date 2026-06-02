# 03: Async Scrape Pipeline — Reliability & State Machine

> **Files:** `app/services/task_executor.py`, `app/services/task_state.py`, `app/services/scraper.py`, `app/services/watchdog.py`  
> **Invariants:** Always-finalize, atomic credit deduction, no zombie tasks

---

## Purpose & Context

### What this solves

Users submit URLs for AI analysis. The pipeline:

1. Creates task (admission)
2. Scrapes URL (background)
3. Calls LLM (background)
4. Returns result

**Key challenges:**

- HTTP request must not wait for scraping/LLM
- Credits only charged if LLM actually runs
- Tasks must never get stuck in limbo

### State Machine

```
PERMISSION_GRANTED → SCRAPING → SCRAPED → LLM_PROCESSING → COMPLETED
                         ↓                      ↓
                      FAILED                 FAILED
```

**Terminal states:** `COMPLETED`, `FAILED`

---

## Design Decisions

### Decision 1: Background processing via FastAPI BackgroundTasks

```python
background_tasks.add_task(execute_scrape_pipeline, task_id, user_id)
```

**Why not Celery?**

- Zero infrastructure for MVP
- Simple, no Redis required
- Easy to migrate later

**Trade-off:** No retry, no job persistence across restarts.

### Decision 2: Credits at LLM phase only

```python
# In task_state.py
async def transition_to_llm_processing(...):
    async with db.begin():
        task.state = TaskState.LLM_PROCESSING
        result = await db.execute(
            "UPDATE users SET credits = credits - 1 WHERE credits > 0"
        )
        if result.rowcount == 0:
            task.state = TaskState.FAILED
            # Rollback happens automatically
```

**Why?**

- Scraping is free (no cost to us)
- LLM calls cost money
- User shouldn't pay for failed scrapes

### Decision 3: Always-finalize pattern

```python
async def execute_pipeline(task_id, user_id):
    try:
        await run_scraping(...)
        await run_llm(...)
        await mark_completed(...)
    except Exception as e:
        await mark_failed(task_id, str(e))
```

**No silent failures. Every path ends in terminal state.**

---

## Code Walkthrough

### State Transitions (`task_state.py`)

Each transition function:

1. Opens transaction
2. Validates current state allows transition
3. Updates state + any data
4. Commits or rolls back

```python
async def transition_to_scraped(task_id, content, db):
    async with db.begin():
        task = await db.get(ScrapeTask, task_id)
        if not task.can_transition_to(TaskState.SCRAPED):
            return TransitionResult(success=False, ...)

        task.state = TaskState.SCRAPED
        task.content = content
```

**Why `can_transition_to()`?**
Prevents invalid transitions like `COMPLETED → SCRAPING`.

### Atomic Credit Deduction

```python
result = await db.execute(text("""
    UPDATE users
    SET credits_remaining = credits_remaining - 1
    WHERE id = :user_id AND credits_remaining > 0
"""), {"user_id": user_id})

if result.rowcount == 0:
    task.state = TaskState.FAILED
    task.error = "Insufficient credits"
```

**Single atomic operation:**

- Check credits > 0
- Decrement
- Fail if not enough

No TOCTOU race possible.

### Scraper (`scraper.py`)

```python
async with httpx.AsyncClient(
    timeout=settings.SCRAPE_TIMEOUT,
    follow_redirects=True,
) as client:
    response = await client.get(url, headers={"User-Agent": settings.USER_AGENT})
```

**Timeout:** Configurable via `SCRAPE_TIMEOUT` setting (default **30 seconds**), enforced by httpx.

**Error handling:**

- `TimeoutException` → `ScrapeError("Scraping timeout after Xs")`
- `HTTPStatusError` → `ScrapeError("HTTP {status_code}")`
- `RequestError` → `ScrapeError("Network error: ...")`
- Any other exception → `ScrapeError("Scraping failed: ...")`

### Watchdog (`watchdog.py`)

```python
async def cleanup_stuck_tasks():
    # Find tasks stuck in SCRAPING > 5 min
    result = await db.execute(
        select(ScrapeTask).where(
            ScrapeTask.state == TaskState.SCRAPING,
            ScrapeTask.updated_at < five_minutes_ago,
        )
    )
    for task in result.scalars().all():
        task.state = TaskState.FAILED
        task.error = "Watchdog: stuck > 5min"
```

**Runs periodically:** Prevents zombie tasks if worker crashes.

---

## Lifecycle & Flow

### Happy Path

```
1. POST /scrape/start
2. Admission: INSERT task (PERMISSION_GRANTED)
3. Return 202 with task_id
4. Background: PERMISSION_GRANTED → SCRAPING
5. Background: Fetch URL (60s timeout)
6. Background: SCRAPING → SCRAPED (store content)
7. Background: SCRAPED → LLM_PROCESSING (deduct credit)
8. Background: Call LLM (120s timeout)
9. Background: LLM_PROCESSING → COMPLETED (store result)
```

### Scraping Fails

```
1-4. Same as above
5. Fetch URL fails (timeout, 404, network error)
6. SCRAPING → FAILED (error = "timeout")
7. Credit NOT deducted
```

### LLM Fails

```
1-7. Same as above
8. LLM API returns error
9. LLM_PROCESSING → FAILED (error = "LLM error")
10. Credit WAS deducted (attempt was made)
```

---

## Concurrency & Failure

### Two requests, same user

```
Request A: Admission succeeds
Request B: Admission fails (unique index)
```

Database handles it atomically.

### Worker crash during SCRAPING

```
1. Task stuck in SCRAPING
2. Watchdog runs (every 60s)
3. Detects task stuck > 5 min
4. Marks FAILED
5. User can start new task
```

### Worker crash during LLM (after credit deduction)

```
1. Task stuck in LLM_PROCESSING
2. Credit was deducted (committed)
3. Watchdog marks FAILED
4. Credit lost — acceptable (attempt was made)
```

---

## Things to Be Careful About

### ⚠️ Don't call LLM outside transaction

```python
# WRONG: Credit deducted but LLM never called if crash here
await transition_to_llm_processing(...)
# crash point
await call_llm(...)

# RIGHT: Our design
# Credit deducted → LLM called → result stored
# All in try/except with mark_failed fallback
```

### ⚠️ Watchdog thresholds matter

```python
SCRAPING_TIMEOUT_MINUTES = 5    # Must be > scraper timeout (60s)
LLM_TIMEOUT_MINUTES = 10        # Must be > LLM timeout (120s)
```

If watchdog is too aggressive, it'll fail working tasks.

### ⚠️ Background tasks don't persist

If server restarts:

- In-progress tasks stay at current state
- Watchdog will eventually clean them up
- For production: migrate to Celery

---

## Future Evolution

### Safe extensions

| Change          | Impact                                      |
| --------------- | ------------------------------------------- |
| Add retry logic | Add `retry_count` column, check in executor |
| Add webhooks    | Call after COMPLETED/FAILED                 |
| Real LLM        | Replace `process_with_llm` stub             |

### Changes requiring care

| Change                      | Why risky                              |
| --------------------------- | -------------------------------------- |
| Multiple LLM calls per task | Credit model needs rethinking          |
| Parallel tasks per user     | Partial unique index needs update      |
| Persistent job queue        | Celery migration, state recovery logic |

---

## Summary

The async scrape pipeline uses FastAPI BackgroundTasks to decouple HTTP requests from long-running operations. The state machine guarantees every task ends in COMPLETED or FAILED (no zombies). Credits are deducted atomically at the LLM phase only — scraping failures are free. The watchdog provides safety against worker crashes.

**Key invariants:**

1. Credit deducted IFF LLM attempted
2. Every task reaches terminal state
3. One active task per user (partial unique index)
4. All failures logged with reason
