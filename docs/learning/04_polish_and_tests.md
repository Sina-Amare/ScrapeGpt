# 04 — Frontend v0 Polish Pass & Test Suite Completion

## Problem / Purpose

Before Phase 1 began (intelligent site analysis, new job state machine,
crawler infrastructure), the existing codebase needed to be made correct,
observable, and verifiably stable. Specifically:

1. The LLM processor was a stub returning hardcoded mock data.
2. The frontend showed task state but none of the AI result content.
3. There were no tests for the scraper, LLM processor, or the new provider
   endpoints added in Phase 0.5.
4. The task list endpoint fetched the full 50 KB `content` column for every row.

This document covers the changes made across backend services, API endpoints,
frontend components, and the test suite during the final polish pass of
Phase 0.5.

---

## Invariants Enforced

- **LLM result is always a validated dict.** `process_with_llm` returns a
  `dict` produced by `ContentAnalysisResult.model_dump()`. Any response from
  the LLM that does not validate against `ContentAnalysisResult` raises
  `LLMError` before the dict is returned — no invalid shapes propagate to the
  `result` column.

- **Content is capped before reaching the LLM.** The scraper caps at 50,000
  characters. `process_with_llm` adds a second cap at `_LLM_CONTENT_LIMIT =
  8,000` characters before building the prompt. This prevents accidental token
  overflows caused by providers with smaller context windows.

- **`content_length` is null on list responses.** The list endpoint uses
  `defer(ScrapeTask.content)` so the column is never loaded for list queries.
  `content_length` is populated only on single-task detail endpoints where the
  full row is loaded. This contract is documented in `TaskResponse` and tested.

- **Normal provider responses never include API keys.** The explicit
  `reveal-key` endpoint returns `{ api_key: str }` only after owner and
  password confirmation. The `api_key_encrypted` bytes are never serialised.
  Tested explicitly in `test_reveal_key_never_exposes_encrypted_blob`.

---

## Design Decisions and Rejected Alternatives

### LLM processor: return dict, not Pydantic model

`process_with_llm` returns `dict[str, Any]` rather than `ContentAnalysisResult`
so the call site (`task_executor.py`) can store it directly as `result=llm_result`
— a plain dict that serialises straight into the JSONB column without an
extra `.model_dump()` call at the executor layer. The validation still happens
inside the processor via Pydantic.

**Rejected:** returning `ContentAnalysisResult` and calling `.model_dump()` in
the executor. This would have coupled the executor to the processor's internal
schema, and any future schema change would require two call sites to update.

### Two-pass provider lookup: default first, then any

`_get_provider(user_id)` issues two separate queries: one for
`is_default=True`, then (if null) one for the first available provider by `id`.
This is two round-trips but keeps the SQL simple.

**Rejected:** a single `ORDER BY is_default DESC LIMIT 1`. The two-pass
approach makes the code easier to read and trace, and the extra round-trip is
negligible because provider lookup is only called once per task lifecycle.

### Content deferred in list endpoint

`defer(ScrapeTask.content)` prevents loading up to 50 KB per row when a user
fetches their task history. The trade-off is that `content_length` cannot be
computed on the list path, so the field is null there.

**Rejected:** computing `content_length` via a `func.length(ScrapeTask.content)`
in the SELECT. While this would populate the stat without loading the bytes,
it adds non-trivial SQL complexity and the stat is of questionable value in a
table view — users who care about the content size will open the detail page.

### `PipelineProgress` treats FAILED as a special case

The state machine has no memory of which stage a task was in when it failed
(`FAILED` is terminal, not a sub-state of `SCRAPING` or `LLM_PROCESSING`). The
`PipelineProgress` component therefore renders FAILED as a full red banner
rather than a partially-filled progress bar, which would be misleading.

**Rejected:** inferring the last good state from timestamps or error messages.
This would be fragile and would encode UI logic that depends on internals of
the state machine.

### Fake session pattern for list-endpoint tests

The existing `FakeSession` only implemented `.execute().scalar_one_or_none()`.
The list endpoint calls `.execute().scalars().all()`. Rather than adding magic
dispatch to a single fake, the tests introduce `FakeListSession` (a separate
class) with explicit `execute → FakeListResult → scalars → FakeScalarsResult`
and `db.get(model, pk)` paths. This keeps each fake small and self-contained.

---

## Code Walkthrough

### `app/services/llm_processor.py`

```
process_with_llm(content, user_id)
  └─ _get_provider(user_id)       ← two-pass DB lookup
       ├─ query default provider for user
       └─ fallback: any provider for user
  └─ content[:_LLM_CONTENT_LIMIT] ← hard cap at 8,000 chars
  └─ call_json_model(provider, messages, ContentAnalysisResult, max_retries=3)
       ← validates response against schema; retries up to 3×
  └─ result.data.model_dump()     ← returns plain dict
```

`call_json_model` is owned by `provider_service` and handles the LiteLLM call,
JSON extraction from the raw response, and schema validation. The processor does
not need to know how retries or JSON extraction work — it gets back a typed
`JSONCallResult` or raises `ProviderCallError`.

On `ProviderCallError`, the processor wraps the error in `LLMError` with the
provider's display name, so the task's `error` column contains a human-readable
message ("Provider 'My OpenAI' failed: ...") rather than an internal exception
string.

### `app/api/v1/endpoints/scrape.py`

`list_tasks` builds its SELECT with:
```python
select(ScrapeTask)
    .options(defer(ScrapeTask.content))
    .where(ScrapeTask.user_id == user.id)
    .order_by(ScrapeTask.created_at.desc())
    .offset(skip)
    .limit(limit)
```

`get_task` and `get_current_task` load the full row via `db.get(ScrapeTask, id)`
and compute `content_length = len(task.content) if task.content else None`.

The `TaskResponse` schema gains `content_length: int | None = None` — nullable
so list responses can omit it without breaking the shape contract.

`skip` and `limit` are validated by FastAPI `Query`:
- `skip: int = Query(default=0, ge=0)`
- `limit: int = Query(default=20, ge=1, le=100)`

Requests with `limit > 100` or `skip < 0` return 422.

### Frontend — `TaskResultPanel`

A shared component consumed by both `DashboardPage` and `NewScrapePage`. The
type guard `isContentAnalysis(r)` checks for the shape of `ContentAnalysisResult`:

```ts
function isContentAnalysis(r: Record<string, unknown>): r is ContentAnalysisResult {
  return (
    typeof r.summary === "string" &&
    Array.isArray(r.key_points) &&
    typeof r.data_type === "string" &&
    typeof r.word_count === "number"
  );
}
```

If the guard passes, it renders a structured card (data_type badge, word count,
summary paragraph, key points list). If the guard fails, it falls back to a
`<pre>` block with `JSON.stringify(result, null, 2)`. This means the component
is safe even if the result column contains an older or malformed shape.

### Frontend — `PipelineProgress`

The pipeline stages map directly to the backend `TaskState` enum:

```
PERMISSION_GRANTED → SCRAPING → SCRAPED → LLM_PROCESSING → COMPLETED
```

Each stage has a pending label (e.g. "Fetching page content…") shown while that
state is active. The `FAILED` state short-circuits to a red alert banner. This
component lives in `NewScrapePage` — `DashboardPage` shows the task state via a
Badge rather than the full progress bar.

---

## Runtime Lifecycle

### Success path

1. `task_executor.py` calls `process_with_llm(content, user_id)`.
2. `_get_provider` loads the user's default provider from the DB.
3. `call_json_model` sends the prompt to the provider (up to 3 retries on
   schema validation failure).
4. A validated `ContentAnalysisResult` is returned.
5. The executor stores `result=llm_result` on the task and transitions to
   `COMPLETED`.
6. The frontend polls `GET /scrape/current-task`, receives the completed task,
   and `TaskResultPanel` renders the structured card.

### Failure paths

- **No provider configured.** `_get_provider` returns `None`. `process_with_llm`
  raises `LLMError("No AI provider configured. Add one in Settings...")`. The
  executor catches `LLMError` in its catch-all and transitions to `FAILED`.
- **Provider call fails after retries.** `call_json_model` raises
  `ProviderCallError`. `process_with_llm` re-raises as `LLMError` with the
  provider name. Task → `FAILED`.
- **LLM response fails schema validation after all retries.** Same path as
  provider call failure.
- **Content truncated.** If the scraped page exceeds 8,000 characters, only the
  first 8,000 are sent to the LLM. No error is raised; the summary may be
  incomplete.

---

## Concurrency / Crash Safety

The LLM processor is stateless — it has no shared mutable state and every
invocation opens its own DB session via `async_session_maker`. Concurrent calls
for different users are independent. The "always-finalize" guarantee in
`task_executor.py` ensures that even if `process_with_llm` raises an unexpected
exception, the task reaches a terminal state.

---

## Test Coverage Added

| File | Tests | What is covered |
|------|-------|----------------|
| `tests/services/test_llm_processor.py` | 9 | No provider, success, truncation, provider failure, error message, user_id threading, fallback, schema validation |
| `tests/services/test_scraper.py` | 12 | Title + body extraction, tag removal, no-title, returns string, truncation, HTTP 404/500, timeout, ConnectError, ScrapeError attributes |
| `tests/api/v1/test_scrape_tasks.py` | 13 | List returns tasks, auth required, SQL uses user filter + ORDER BY + LIMIT, skip/limit params, invalid limit/skip 422, content_length null on list, error on FAILED, get_task content_length, content_length None when no content, 404 cross-user, 404 not found, result on COMPLETED |
| `tests/api/v1/test_providers_extended.py` | 13 | List empty, list all (no key leakage), delete 204, delete 404, reveal auth required, reveal password required, reveal key decrypted, reveal key 404, encrypted blob never in response, test capability flags, test failure detail, test 404 |

Total backend suite after this polish pass was **92 passing tests**. The current
project suite is larger after Phase 1; see `docs/STATUS.md` for the latest
verified counts.

---

## Pitfalls

### `fake_get_config` must use keyword argument names

`_get_owned_provider_or_404` calls:
```python
await provider_service.get_provider_config(db, user_id=user.id, provider_config_id=provider_id)
```

A fake with positional parameter names like `(_db, _uid, _pid)` will fail with
`TypeError: unexpected keyword argument 'user_id'`. The correct signature is:
```python
async def fake_get_config(_db, user_id, provider_config_id): ...
```

### Async mocks for `list_provider_configs`

`list_provider_configs` is `async`. Monkeypatching it with a sync lambda and
then `await`-ing the result raises `TypeError`. Always use `async def` in the
fake.

### `Badge` does not accept `className`

Wrapping the Badge in a `<div className="shrink-0">` is the correct fix — do
not pass `className` directly to `Badge`.

---

## Safe Evolution Notes

- Adding a new `ContentAnalysisResult` field: add it with a default value so
  older stored results (that pre-date the field) still deserialise correctly.
  The `isContentAnalysis` type guard in the frontend only checks the fields it
  renders — adding optional fields to the backend schema does not require a
  frontend change.
- Changing `_LLM_CONTENT_LIMIT`: update the test
  `test_process_with_llm_truncates_content_to_limit` which asserts on the exact
  length of the content passed to the model.
- Changing `list_tasks` default page size: update
  `test_list_tasks_query_uses_user_filter_and_ordering` if it inspects LIMIT
  values directly.
- Removing the `defer(ScrapeTask.content)` from the list endpoint would cause
  `content_length` to become non-null on list responses — update the test
  `test_list_tasks_does_not_include_content_length` if this is ever intentional.
