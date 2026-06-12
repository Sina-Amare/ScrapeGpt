# 02 — Phase 0.5: Provider Foundation & Credit Removal

> **Files touched:**
> `alembic/versions/005_provider_foundation.py` · `app/models/user.py` · `app/models/provider_config.py` ·
> `app/core/config.py` · `app/services/provider_service.py` · `app/api/v1/endpoints/providers.py` ·
> `app/services/admission.py` · `app/services/task_state.py` · `app/core/scheduler.py` ·
> `app/services/readiness.py` · `app/schemas/auth.py` · `app/api/v1/router.py` · `.env.example`

---

## 1. Why This Phase Exists

The original ScrapGPT backend was a credit-gated SaaS product. Every user had a `credits_remaining`
counter and a daily limit. A background APScheduler job reset credits at midnight. A partial unique
index in PostgreSQL prevented any user from having more than one active task at a time. Credits were
deducted atomically at the `SCRAPED → LLM_PROCESSING` transition.

The redesign throws all of that out. The new model is:

- **Self-hosted, no billing.** The operator (whoever runs the server) sets resource limits in `.env`.
- **BYOK (Bring Your Own Key).** Users connect their own AI provider credentials. The platform stores
  and uses them; it does not pay for API calls.
- **Multiple concurrent jobs allowed.** `MAX_CONCURRENT_JOBS_PER_USER` (default 3) replaces the
  one-task-per-user hard limit.

Phase 0.5 is primarily a foundation reset: strip the old model, lay the new one,
keep the existing pipeline working, and make sure nothing breaks. It also adds
the provider-management API surface that the frontend later exposes.

---

## 2. Everything That Changed — The Full Map

### 2.1 The Database Schema (`005_provider_foundation.py`)

**Dropped from `users` table:**

| Column | What it was |
|--------|-------------|
| `credits_remaining` | Integer counter, decremented at LLM phase |
| `daily_credit_limit` | Max credits per day (default 5) |
| `credits_reset_at` | Timestamp of last reset |

**Dropped entirely:** the `system_state` table (a single-row key/value table used to track
`last_credit_reset` and implement a check-and-set so only one server process reset credits
on any given day).

**Dropped index:** `ix_one_active_task_per_user` — the PostgreSQL partial unique index
`WHERE state NOT IN ('COMPLETED', 'FAILED')` on `scrape_tasks(user_id)`. This was the
concurrency safety net for the one-task invariant. It is gone because the invariant is gone.

**Added to `users` table:**

| Column | What it is |
|--------|------------|
| `default_provider_id` | Nullable FK → `provider_configs.id` (ON DELETE SET NULL) |

**New table: `provider_configs`**

| Column | Purpose |
|--------|---------|
| `id` | PK |
| `user_id` | FK → `users.id` (ON DELETE CASCADE) |
| `name` | Human display name (e.g. "My OpenAI key") |
| `provider` | LiteLLM provider string (e.g. `openai`, `anthropic`, `ollama`) |
| `model` | Model identifier (e.g. `gpt-4o-mini`, `claude-3-5-sonnet`) |
| `api_key_encrypted` | Fernet-encrypted API key, stored as text |
| `is_default` | Boolean — at most one true per user (enforced by partial unique index) |
| `capability_flags` | JSONB — result of the provider connectivity test |
| `created_at`, `updated_at` | Timestamps from `TimestampMixin` |

**New indexes on `provider_configs`:**
- `ix_provider_configs_user_id` — fast lookup of all configs for a user
- `ix_provider_configs_provider` — useful for analytics / filtering by provider
- `ix_provider_configs_one_default_per_user` — partial unique index `WHERE is_default = true`,
  one row per user. This is the last-resort concurrency guard (see section 4).

**Why the circular FK is safe.** `users.default_provider_id → provider_configs.id` and
`provider_configs.user_id → users.id` form a cycle. PostgreSQL handles this correctly:
when a user is deleted, the CASCADE on `provider_configs.user_id` deletes their configs;
the SET NULL on `users.default_provider_id` clears the pointer first. SQLAlchemy's
`post_update=True` on the `User.default_provider` relationship tells the ORM to issue
an UPDATE after the initial INSERT flush order, avoiding circular flush dependency errors.

---

### 2.2 Configuration (`app/core/config.py`)

**Removed settings:** `DEFAULT_DAILY_CREDITS`, `SCRAPE_CREDIT_COST`.

**Added settings:**

| Setting | Default | Role |
|---------|---------|------|
| `PROVIDER_KEY_ENCRYPTION_SECRET` | — (required, no default) | Fernet key for API key encryption |
| `MAX_CONCURRENT_JOBS_PER_USER` | 3 | Replaces the one-task invariant |
| `MAX_PAGES_PER_JOB` | 500 | Per-job page cap (used in Phase 2) |
| `CRAWL_CONCURRENCY` | 3 | Concurrent fetches per job (Phase 2) |
| `MIN_CRAWL_DELAY_MS` | 500 | Politeness delay per domain (Phase 2) |
| `JOB_QUEUE_DEPTH` | 10 | Max queued jobs per user (Phase 2) |
| `LLM_TIMEOUT` | 120 | LiteLLM call timeout in seconds |

**`PROVIDER_KEY_ENCRYPTION_SECRET` validation** runs at startup via a Pydantic
`@field_validator`. It calls `Fernet(v.encode())` inside a `try/except`. If the value is
missing, empty, not a valid 44-character URL-safe base64 string, or decodes to anything other
than 32 bytes, `Fernet(...)` raises and the validator re-raises as `ValueError`, which Pydantic
surfaces as a `ValidationError` — crashing the process before it serves a single request.

This is intentional. A malformed encryption key in production means every provider API call
would fail with a decryption error. Failing at startup is far better than failing silently at
runtime and corrupting user data.

---

### 2.3 Provider Config Service (`app/services/provider_service.py`)

This is the most complex new file. It has three responsibilities:

**A. Encryption / Decryption**

```python
def encrypt_api_key(api_key: str) -> str:
    return Fernet(settings.PROVIDER_KEY_ENCRYPTION_SECRET.encode()).encrypt(
        api_key.encode()
    ).decode()
```

Fernet produces a token that includes: version byte, 128-bit IV, HMAC-SHA256 authentication tag,
and the AES-128-CBC ciphertext. The result is URL-safe base64. It is *authenticated* — tampering
with the stored value raises `InvalidToken` rather than silently decrypting garbage.

The API key is decrypted in two controlled paths: provider calls, where it is passed directly to
`litellm.acompletion` as the `api_key` kwarg, and the explicit reveal endpoint, which requires
owner scope plus password confirmation. Normal provider responses never include key material,
and provider errors are redacted before they touch logs, prompts, responses, or task errors.

**B. CRUD with default-provider management**

Creating, updating, and deleting provider configs all need to maintain the invariant:
*at most one provider per user has `is_default=True`.*

The steps for `create_provider_config` are:
1. Acquire a per-user advisory lock (`_lock_user_provider_configs`).
2. Count existing configs. If zero, this is the first provider — auto-set as default.
3. If setting as default: run `_clear_default_provider` (UPDATE all configs for this user
   to `is_default=False`, set `user.default_provider_id=None`).
4. INSERT the new config.
5. FLUSH (assigns the new `id` without committing).
6. If default: set `user.default_provider_id = provider_config.id`.
7. COMMIT.

The advisory lock (step 1) serializes concurrent requests for the same user. Without it, two
simultaneous first-provider requests could both see count=0, both set `is_default=True`, and the
second INSERT would hit the partial unique index and raise an `IntegrityError`. The advisory lock
prevents that. The index is the fallback if the lock is somehow bypassed (e.g. a manual DB insert).

**C. The LiteLLM JSON Pipeline (`call_json_model`)**

Calling an AI provider and getting reliable structured JSON back is harder than it looks.
Not every provider supports `response_format={"type": "json_object"}`. Even when they do,
the response may include markdown fences, preamble text, or schema violations.

The pipeline handles this in order, up to `max_retries` (default 3) attempts total:

```
Attempt N:
  1. Call provider with response_format={"type": "json_object"} (native JSON mode)
  2. If ProviderCallError (network/API error) and native JSON was used:
       → flip to strict-prompt mode, continue loop
  3. If ProviderCallError and already in strict-prompt mode:
       → re-raise (no point retrying a network failure with a different prompt)
  4. If response received: strip markdown fences, find outermost { } or [ ]
  5. JSON-parse the extracted substring
  6. Pydantic-validate against the expected schema
  7. If parse or validation fails:
       → flip to strict-prompt mode, append clarifying instruction, continue loop

After max_retries exhausted → raise ProviderJSONError with the raw response attached
```

Strict-prompt mode appends a message: *"Output ONLY raw JSON conforming to this schema.
No markdown, no preamble..."* with the full JSON schema embedded. This works well for models
that understand instructions but have JSON mode disabled or unreliable.

Known limitation: `extract_json_payload` uses `find("{")` and `rfind("}")` to locate JSON.
If a response contains two separate JSON objects (e.g. a context object and the actual result),
the slice spans both — invalid JSON — and the function raises `ProviderJSONError`. The retry
with the strict prompt then produces a clean single-object response. Not a bug; a one-shot
recoverable failure.

---

### 2.4 Provider API Endpoints (`app/api/v1/endpoints/providers.py`)

Six routes under `/api/v1/providers`, all requiring `get_current_user`:

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/providers` | List all configs for the current user |
| POST | `/providers` | Create a config (API key write-only) |
| PATCH | `/providers/{id}` | Update fields; API key replaced only if provided |
| DELETE | `/providers/{id}` | Delete; clears `default_provider_id` if needed |
| POST | `/providers/{id}/test` | Probe connectivity and capability |
| POST | `/providers/{id}/reveal-key` | Reveal own key after password confirmation |

**Critical invariant in normal responses:** `ProviderConfigResponse` has no `api_key` or
`api_key_encrypted` field. The ORM model has `api_key_encrypted` but the Pydantic response
schema deliberately omits it. Even if the service accidentally returned the raw ORM object,
FastAPI would serialize only the fields declared in the response model. The only endpoint that
can return plaintext key material is `POST /providers/{id}/reveal-key`, and it requires password
confirmation before decrypting.

**`IntegrityError` handling:**
```python
try:
    provider_config = await provider_service.create_provider_config(db, user.id, payload)
except IntegrityError:
    await _raise_provider_conflict(db)
```

`_raise_provider_conflict` explicitly calls `await db.rollback()` before raising 409. This is
important: after an `IntegrityError`, the SQLAlchemy session is in a "rolled back internally"
state. Without an explicit rollback, subsequent operations on the same session would fail with
`InvalidRequestError: Can't reconnect until invalid transaction is rolled back`. The rollback
resets the session to a usable state for any error handlers that run after the 409.

**`/providers/{id}/test`** calls `test_provider_config`, which calls `call_json_model` with a
minimal probe schema `{"ok": true}`. The result is stored in `capability_flags` and committed
regardless of success or failure. Flags tell the rest of the system whether this provider supports
native JSON mode, connectivity succeeded, etc.

---

### 2.5 Admission Service (`app/services/admission.py`)

Before: checked `credits_remaining >= SCRAPE_CREDIT_COST` AND `no existing non-terminal task`.
After: checks `active_task_count < MAX_CONCURRENT_JOBS_PER_USER` only.

The count query and INSERT are still serialized by `pg_advisory_xact_lock(user.id)` — the
same single-argument form as before. The advisory lock is a *transaction-level* lock: acquired
at the first `db.execute`, held until `db.commit()` or `db.rollback()`. This prevents a
concurrent second request from also seeing count=0 and both inserting tasks simultaneously.

If the limit is reached, the function calls `db.rollback()` explicitly (releasing the lock
immediately) and returns `AdmissionError(TOO_MANY_ACTIVE_TASKS)`. The endpoint maps this to 409.

**What's gone:** `INSUFFICIENT_CREDITS` error type, credit column queries, the old partial unique
index as a safety net. The count-based check is now the sole enforcement mechanism. There is no
database-level constraint to catch races — just the advisory lock.

---

### 2.6 Task State Service (`app/services/task_state.py`)

`transition_to_llm_processing` used to do this inside one transaction:
```sql
UPDATE users SET credits_remaining = credits_remaining - 1 WHERE id = :user_id AND credits_remaining >= 1
```
If `rowcount == 0` (no credits), it would mark the task FAILED and return early.

Now it just transitions the state. No credit check, no SQL UPDATE on users. The function is
simpler: validate ownership, check the transition is legal, set `state = LLM_PROCESSING`, commit.

The "always-finalize guarantee" is unchanged — every task still reaches COMPLETED or FAILED.
Credits were the only reason a task could fail at the LLM_PROCESSING gate that wasn't a real
error in the pipeline. Removing them makes the success path simpler.

---

### 2.7 Scheduler (`app/core/scheduler.py`)

Before: two jobs — `run_watchdog_once` (every 60s) and `try_reset_all_credits` (daily at 00:00 UTC).

After: one job — `run_watchdog_once` only.

The credit reset job used a check-and-set on the `system_state` table to avoid double-resets when
multiple server instances ran simultaneously. That table is gone, the job is gone, the APScheduler
`CronTrigger` for it is gone.

---

### 2.8 Readiness Service (`app/services/readiness.py`)

The `_run_probe` function SELECT-probes specific columns to verify the schema is migrated correctly.
Before, it probed `credits_remaining` on `users` and the `system_state` table.

After migration 005, it probes `default_provider_id` on `users` and `api_key_encrypted`
on `provider_configs`. Phase 1 later extends the same readiness probe to cover `jobs`
and `analysis_cache`. If you try to start the server against a database that has not
had the relevant migrations applied, the probe fails with `schema_incompatible`.

---

### 2.9 Auth / Scrape Schemas

`UserResponse` (returned after register/login) used to include `credits_remaining`. It now includes
`default_provider_id: int | None`. The scrape schemas lost their credit-related fields too.
No API contracts were broken because the fields were additive SaaS-specific data that no standard
client would have depended on in a BYOK context.

---

## 3. Data Flow: Creating a Provider Config

```
POST /api/v1/providers
  Body: { name, provider, model, api_key, is_default }

→ FastAPI: deserialize into ProviderConfigCreate (Pydantic validates field lengths)
→ get_current_user dep: verify JWT, fetch User from DB
→ get_db dep: yield AsyncSession

→ providers.create_provider()
    → provider_service.create_provider_config(db, user.id, payload)
        → pg_advisory_xact_lock(47005, user.id)   [serializes concurrent writes per user]
        → SELECT COUNT(*) WHERE user_id = ?        [is this the first provider?]
        → if default: UPDATE provider_configs SET is_default=False WHERE user_id=?
                       UPDATE users SET default_provider_id=NULL WHERE id=?
        → encrypt_api_key(payload.api_key)         [Fernet AES-128-CBC + HMAC-SHA256]
        → INSERT provider_configs (all fields)
        → FLUSH                                    [assigns .id without committing]
        → if default: UPDATE users SET default_provider_id = new_config.id
        → COMMIT                                   [lock released, all changes durable]
    → return ProviderConfig ORM object

→ ProviderConfigResponse.model_validate(provider_config)
    [api_key_encrypted NEVER appears in this schema]

→ HTTP 201 { id, name, provider, model, is_default, capability_flags, created_at, updated_at }
```

---

## 4. Invariants That Must Not Break

| Invariant | Where enforced | What breaks if violated |
|-----------|---------------|------------------------|
| At most one default provider per user | Advisory lock + partial unique index | `default_provider_id` points to the wrong provider; second INSERT fails with IntegrityError |
| Normal provider responses never return API keys | `ProviderConfigResponse` schema has no key field; reveal endpoint requires password | Provider key material in logs / client storage / caches |
| `PROVIDER_KEY_ENCRYPTION_SECRET` never changes without migrating data | Startup validator; `.env.example` warning | All stored keys become permanently unreadable (Fernet `InvalidToken`) |
| Active task count checked under advisory lock | `admission.py` | Two simultaneous requests both pass the count check; user gets extra task |
| Session is explicitly rolled back after `IntegrityError` | `_raise_provider_conflict` | Session left in invalid transaction state; next operation raises `InvalidRequestError` |

---

## 5. Concurrency and Crash Safety

**Two simultaneous `POST /providers` with no existing configs (race):**
1. Both acquire `pg_advisory_xact_lock(47005, user.id)` — one waits.
2. First completes: inserts with `is_default=True`, commits, lock released.
3. Second runs: sees count=1 (not first), checks `payload.is_default`. If False → inserts
   without setting default. If True → clears the first default, sets itself as default. No conflict.

**App crash mid-create-provider:**
The COMMIT never arrives. PostgreSQL rolls back the open transaction. `provider_configs` has no
partial row. `users.default_provider_id` is unchanged. On restart, user has no new provider — they
retry the POST. No data corruption.

**`PROVIDER_KEY_ENCRYPTION_SECRET` rotation:**
The startup validator would need to be temporarily disabled during a rotation, or a migration
script run that decrypts all keys with the old secret and re-encrypts with the new one before
the new secret is deployed. The `.env.example` documents this explicitly. Key rotation is a
management operation, not a normal deploy step.

---

## 6. Topics to Study

These are the minimum concepts behind each piece of the implementation. The goal is not to become
an expert in each — just to understand what the code is doing and why, well enough to extend or
debug it.

### Cryptography
- **Fernet symmetric encryption** — what the Fernet spec provides (authenticated encryption,
  AES-128-CBC, HMAC-SHA256, versioned tokens). Why "authenticated" matters (tampering raises an
  exception rather than silently returning garbage). Python `cryptography` library docs:
  `cryptography.fernet.Fernet`.
- **Why a separate encryption key from the JWT secret** — defense in depth: compromising one
  key does not compromise the other. The concept of *key separation*.
- **Base64 URL-safe encoding** — Fernet keys are 32 random bytes encoded as base64url (44 chars).
  Understanding this helps when you see the key format and write the generation command.

### PostgreSQL Advisory Locks
- **Session-level vs. transaction-level advisory locks** — `pg_advisory_xact_lock` (released at
  transaction end) vs. `pg_advisory_lock` (released manually or on session close). This codebase
  uses transaction-level everywhere.
- **Single-argument vs. two-argument form** — `pg_advisory_xact_lock(bigint)` vs.
  `pg_advisory_xact_lock(int4, int4)`. The two-argument form lets you namespace locks by concern
  (e.g. `47005` = provider configs, `admission` = task admission).
- **TOCTOU (Time-of-Check to Time-of-Use)** — the general race condition pattern. The advisory
  lock closes the gap between checking a count and inserting a row.

### SQLAlchemy Async Session Lifecycle
- **autobegin** — SQLAlchemy 2.0 starts a transaction implicitly on the first DML or SELECT.
  You don't call `db.begin()` explicitly in the provider service — the first `db.execute` starts
  the transaction.
- **`expire_on_commit=False`** — why the session factory sets this, and what the alternative
  ("detached instance error") looks like.
- **`autoflush=False`** — why it is False, and what `db.flush()` does explicitly (sends pending
  ORM changes to the DB within the current transaction, assigns auto-generated IDs, without
  committing).
- **`post_update=True` on relationships** — how SQLAlchemy handles circular FK references during
  flush ordering. Required when two tables reference each other with FKs (users ↔ provider_configs).
- **Identity map** — within a single session, `db.get(User, id)` twice returns the same Python
  object. This is why `_clear_default_provider` can modify `user.default_provider_id` and
  `create_provider_config` sees that modification without re-querying.

### LiteLLM
- **What LiteLLM is** — a unified API over 100+ LLM providers. One call signature, any provider.
  Relevant docs: `litellm.acompletion`, the `model` parameter format (`provider/model-name`),
  and the `response_format={"type": "json_object"}` kwarg.
- **Why not all providers support JSON mode** — GPT-4o supports it natively; Anthropic uses tool
  calls; Ollama models often ignore it. This is why the fallback strict-prompt path exists.
- **`custom_llm_provider` kwarg** — tells LiteLLM which provider routing to use when the model
  string alone is ambiguous.

### Pydantic v2
- **`@field_validator`** — class method that validates a single field at settings load time.
  Used for the Fernet key startup check.
- **`model_dump(exclude_unset=True)`** — returns only fields that were explicitly set by the
  caller (not default values). Used in `update_provider_config` to implement PATCH semantics:
  only update fields that were provided.
- **`from_attributes=True` in `model_config`** — allows Pydantic to build a response model
  from a SQLAlchemy ORM object by reading attributes instead of dict keys.

### Alembic Migrations
- **`op.execute()` for raw SQL** — when Alembic's `op.*` helpers don't cover your case (e.g.
  `DROP INDEX IF EXISTS`, `CREATE UNIQUE INDEX ... WHERE ...`), raw SQL via `op.execute` is
  the right tool.
- **`ondelete="CASCADE"` vs. `ondelete="SET NULL"`** — PostgreSQL FK ON DELETE behavior.
  Cascade deletes the dependent row; SET NULL nullifies the FK column. Both are used here.
- **Partial unique indexes** — `CREATE UNIQUE INDEX ... WHERE is_default = true`. PostgreSQL
  only enforces uniqueness among rows matching the WHERE clause. Rows with `is_default=false`
  don't compete for the index slot.
- **Why some DDL can't run inside a transaction** — `ALTER TYPE ... ADD VALUE` (adding a new
  enum value) cannot run inside a transaction in PostgreSQL. This is documented in the CLAUDE.md
  gotchas. Dropping/creating indexes and tables CAN run inside a transaction (as this migration
  does).

### FastAPI Dependency Injection
- **How `get_db` works** — it's an async generator. FastAPI calls `next()` to yield the session
  before the route handler runs, and calls `close()` in the `finally` block after the handler
  completes (whether it succeeded or raised). This guarantees the session is always closed.
- **Dependency override in tests** — `app.dependency_overrides[deps.get_db] = fake_db_factory`
  replaces the real DB dependency for a specific test without affecting other tests. Used
  extensively in `test_providers.py`.
- **`HTTPException` propagation** — raising `HTTPException` inside a dependency or route handler
  goes through FastAPI's exception handler, which serializes the `detail` as JSON and returns
  the right status code. The `finally` in `get_db` still runs (session is closed).
