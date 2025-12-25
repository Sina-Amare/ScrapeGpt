# 01: ScrapeTask Schema & Invariant Enforcement

> **Files:** `app/models/scrape_task.py`, `alembic/versions/002_create_scrape_tasks.py`  
> **Invariant:** At most one non-FINALIZED task per user

---

## Purpose & Context

### What problem this solves

Users submit URLs for scraping and LLM analysis. Each scraping job goes through multiple states before completion. We need to:

1. Track job progress through states
2. Prevent users from starting multiple jobs simultaneously (resource control)
3. Allow completed jobs to accumulate (history)

### Why this exists

Without this constraint, a user could:

- Spam unlimited scrape requests
- Overwhelm LLM resources
- Create race conditions in downstream processing

The invariant "at most one non-FINALIZED task per user" forces sequential processing per user while allowing parallel processing across different users.

### The invariant

```
For any user_id, COUNT(tasks WHERE state != 'FINALIZED') <= 1
```

This is enforced at the **database level**, not application level.

---

## Design Decisions

### Decision 1: Partial unique index (chosen)

```sql
CREATE UNIQUE INDEX ix_one_active_task_per_user
ON scrape_tasks (user_id)
WHERE state != 'FINALIZED';
```

**Alternatives considered:**

| Option                                      | Description                         | Why rejected                                                                      |
| ------------------------------------------- | ----------------------------------- | --------------------------------------------------------------------------------- |
| Application-level check                     | `SELECT COUNT(*) ... BEFORE INSERT` | Race conditions: two requests could check simultaneously, both see 0, both insert |
| Full unique index on `(user_id, is_active)` | Add boolean column                  | Requires maintaining another field; partial index is cleaner                      |
| Database trigger                            | `BEFORE INSERT` trigger with check  | More code, same result, harder to debug                                           |

**Trade-off accepted:** Partial indexes are PostgreSQL-specific. If we migrate to MySQL, we'd need triggers.

### Decision 2: Enum for states

```python
class TaskState(str, enum.Enum):
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    SCRAPED = "SCRAPED"
    LLM_ANALYZED = "LLM_ANALYZED"
    OUTPUT_GENERATION = "OUTPUT_GENERATION"
    FINALIZED = "FINALIZED"
```

**Why `str, enum.Enum`?**

- `str` inheritance means `TaskState.SCRAPED == "SCRAPED"` is `True`
- JSON serialization works automatically
- PostgreSQL enum maps cleanly to string values

**Why native PostgreSQL enum (not VARCHAR)?**

- Type safety at database level
- Invalid values rejected by PostgreSQL
- Slight storage efficiency

### Decision 3: `ON DELETE CASCADE`

```sql
user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
```

When a user is deleted, all their tasks are deleted automatically. Alternative would be `SET NULL` or blocking delete, but orphaned tasks have no value.

---

## Code Walkthrough

### The Model (`app/models/scrape_task.py`)

```python
class ScrapeTask(Base):
    __tablename__ = "scrape_tasks"
```

**`__tablename__`**: Explicit table name. Without this, SQLAlchemy uses class name (`scrapetask`).

---

```python
id: Mapped[int] = mapped_column(
    Integer,
    primary_key=True,
    autoincrement=True,
)
```

**`Mapped[int]`**: Type hint for SQLAlchemy 2.0 style. Tells type checkers this is an int.  
**`mapped_column()`**: SQLAlchemy 2.0 replacement for `Column()`.  
**`autoincrement=True`**: PostgreSQL uses `SERIAL` internally.

---

```python
user_id: Mapped[int] = mapped_column(
    Integer,
    ForeignKey("users.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
)
```

**`ForeignKey("users.id")`**: References the `id` column of `users` table by string (no import cycle).  
**`index=True`**: Creates `ix_scrape_tasks_user_id` for fast user-based queries.

---

```python
state: Mapped[TaskState] = mapped_column(
    Enum(TaskState, name="task_state", native_enum=True),
    nullable=False,
    default=TaskState.PERMISSION_GRANTED,
    index=True,
)
```

**`native_enum=True`**: Use PostgreSQL's native `ENUM` type instead of VARCHAR with check constraint.  
**`name="task_state"`**: The PostgreSQL type name. Must match the migration.  
**`index=True`**: For queries like `WHERE state = 'SCRAPED'`.

---

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
    server_default=func.now(),
    nullable=False,
)
```

**Why both `default` and `server_default`?**

- `default`: Python-side default when creating objects in memory
- `server_default`: SQL-side default for raw INSERTs or migrations

**`timezone=True`**: Creates `TIMESTAMPTZ` column in PostgreSQL.

---

```python
updated_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    onupdate=func.now(),
    nullable=True,
)
```

**`onupdate=func.now()`**: SQLAlchemy sets this on every UPDATE. But note: this is Python-side, not SQL trigger. Raw SQL updates won't set this.

---

### The Migration (`002_create_scrape_tasks.py`)

```sql
DO $$ BEGIN
    CREATE TYPE task_state AS ENUM (...);
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;
```

**Why the exception handler?**
If migration fails halfway and is retried, the enum type might already exist. This makes the migration **idempotent**.

---

```sql
CREATE UNIQUE INDEX ix_one_active_task_per_user
ON scrape_tasks (user_id)
WHERE state != 'FINALIZED';
```

**The magic line.** This is a **partial unique index**:

- Only indexes rows WHERE the condition is true
- Unique constraint only applies to those rows
- FINALIZED tasks are excluded from the index entirely

---

## Lifecycle & Flow

### Creating a new task (happy path)

```
1. User authenticated via JWT
2. Application creates: ScrapeTask(user_id=1, url="https://...")
3. SQLAlchemy issues: INSERT INTO scrape_tasks (user_id, state, url) VALUES (1, 'PERMISSION_GRANTED', '...')
4. PostgreSQL checks partial unique index:
   - Scans index for user_id=1 WHERE state != 'FINALIZED'
   - No existing row found → INSERT succeeds
5. Task created with id=42
```

### Creating a second task (user already has active task)

```
1. User has existing task: id=42, state=SCRAPED (not FINALIZED)
2. User tries to create another task
3. PostgreSQL checks partial unique index:
   - Finds existing row with user_id=1 WHERE state != 'FINALIZED'
   - Unique violation → INSERT rejected
4. Application receives: IntegrityError (UniqueViolation)
5. Application returns 409 Conflict to user
```

### Completing a task

```
1. Task id=42 is in state OUTPUT_GENERATION
2. Application issues: UPDATE scrape_tasks SET state='FINALIZED' WHERE id=42
3. PostgreSQL updates the row
4. Partial unique index REMOVES this row from the index (state is now FINALIZED)
5. User can now create a new task (index is empty for this user_id)
```

### On failure

| Failure point                   | What happens                                     |
| ------------------------------- | ------------------------------------------------ |
| INSERT fails (unique violation) | Transaction rolls back, no task created          |
| Process crashes mid-INSERT      | PostgreSQL rolls back uncommitted transaction    |
| UPDATE to FINALIZED fails       | Task stays in previous state, user still blocked |

---

## Concurrency & Failure Analysis

### Race condition: Two simultaneous INSERTs

```
Request A: INSERT ... (user_id=1, state='PERMISSION_GRANTED')
Request B: INSERT ... (user_id=1, state='PERMISSION_GRANTED')

Both hit PostgreSQL at the same time.
```

**What happens:**

1. PostgreSQL acquires row-level lock on the index entry
2. Request A acquires lock first, INSERT succeeds
3. Request B waits for lock, then checks index, finds A's row
4. Request B fails with unique violation

**Result:** Only one task created. Database guarantees atomicity.

### Race condition: UPDATE and INSERT

```
Request A: UPDATE task SET state='FINALIZED' WHERE id=42
Request B: INSERT new task for same user
```

**Scenario 1: A commits first**

- A removes row from index
- B's INSERT succeeds

**Scenario 2: B runs before A commits**

- B sees old state in index (A not committed yet)
- B fails with unique violation
- B should retry after A commits

**Key insight:** The partial unique index uses PostgreSQL's MVCC. If A hasn't committed, B still sees the old row in the index.

### Process crash scenarios

| Crash point                                | State after restart                     |
| ------------------------------------------ | --------------------------------------- |
| During INSERT (before commit)              | No task exists, index clean             |
| During UPDATE to FINALIZED (before commit) | Old state preserved, user still blocked |
| After commit                               | New state persisted correctly           |

---

## Things To Be Careful About

### ⚠️ Never rename enum values casually

```python
# DON'T DO THIS without a migration:
SCRAPED = "CONTENT_FETCHED"  # Breaks existing database rows!
```

Enum values are stored as strings. Changing them requires a database migration.

### ⚠️ The partial index condition must match your logic

```sql
WHERE state != 'FINALIZED'
```

If you add a new terminal state (e.g., `CANCELLED`), update the index:

```sql
WHERE state NOT IN ('FINALIZED', 'CANCELLED')
```

### ⚠️ Raw SQL updates won't trigger `onupdate`

```python
# This sets updated_at:
task.state = TaskState.SCRAPED
await db.commit()

# This does NOT set updated_at:
await db.execute(text("UPDATE scrape_tasks SET state='SCRAPED' WHERE id=1"))
```

### ⚠️ Don't trust application-level checks alone

```python
# WRONG: Race condition possible
if not await has_active_task(user_id):
    await create_task(user_id)  # Two requests can both pass the check

# RIGHT: Let the database enforce
try:
    await create_task(user_id)
except IntegrityError:
    raise HTTPException(409, "Already have active task")
```

---

## Future Evolution

### Safe extensions

| Change                                       | Impact                                         |
| -------------------------------------------- | ---------------------------------------------- |
| Add new intermediate states                  | Safe. Just add to enum, no index change needed |
| Add metadata columns (e.g., `error_message`) | Safe. Just add nullable column                 |
| Add another terminal state                   | Requires index update to exclude it            |

### Changes requiring rethinking

| Change                                | Why it's risky                            |
| ------------------------------------- | ----------------------------------------- |
| Allow 2 active tasks per user         | Rebuild index with different constraint   |
| Remove the invariant entirely         | Drop the index (easy) but lose protection |
| Add version_id for optimistic locking | Migration needed, but backward compatible |

---

## Summary

The `scrape_tasks` table uses a **partial unique index** to enforce that each user can have at most one non-FINALIZED task. This enforcement happens at the **database level**, making it immune to application-level race conditions.

**Key points to remember:**

1. The index only covers rows where `state != 'FINALIZED'`
2. FINALIZED tasks are excluded from the constraint, allowing unlimited history
3. Concurrent INSERTs are handled atomically by PostgreSQL
4. Adding new intermediate states is safe; adding new terminal states requires index update
5. Never bypass the ORM without understanding the implications for `updated_at` and other Python-side behaviors

This implementation is correct because it delegates the invariant to the database, which provides ACID guarantees that application code cannot.
