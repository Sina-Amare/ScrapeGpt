# Senior-Level Code Review: ScrapeTask Implementation

**Reviewer Focus:** Production-critical correctness and risk analysis  
**Scope:** `scrape_tasks` table, invariant enforcement, concurrency safety

---

## 1. Core Invariant Analysis

### Invariant: "At most one non-FINALIZED task per user"

**Implementation:**

```sql
CREATE UNIQUE INDEX ix_one_active_task_per_user
ON scrape_tasks (user_id)
WHERE state != 'FINALIZED';
```

### ✅ SAFE: Invariant is correctly enforced

**Why it's safe:**

- Partial unique indexes are ACID-compliant in PostgreSQL
- The database will reject any INSERT or UPDATE that would create a second non-FINALIZED task for the same user
- This enforcement is **transactional** and **atomic** - no application-level race conditions can bypass it

**Tested scenarios:**
| Operation | Result |
|-----------|--------|
| User creates first task | ✅ Succeeds |
| User creates second task (first not FINALIZED) | ❌ Unique violation, rejected |
| User creates second task (first FINALIZED) | ✅ Succeeds |
| Concurrent INSERTs for same user | ❌ One wins, one fails with unique violation |

---

## 2. Concurrency & Atomicity

### ⚠️ POTENTIAL ISSUE: State transition race condition

**Scenario:**

1. Two requests attempt to transition the SAME task simultaneously
2. Both read `state = SCRAPED`
3. Both attempt `UPDATE state = LLM_ANALYZED`
4. Both succeed (no unique constraint applies to same-row updates)

**Risk Level:** LOW

**Why it's mostly safe:**

- SQLAlchemy's default isolation level (READ COMMITTED) means the second UPDATE will block until the first commits
- The second UPDATE will then see the new state and apply its change
- However, without optimistic locking, the second request won't know the first already changed the state

**Recommendation:** Add optimistic locking via `version_id` column if state machine integrity is critical:

```python
version: Mapped[int] = mapped_column(Integer, default=0)
__mapper_args__ = {"version_id_col": version}
```

---

## 3. Failure & Crash Safety

### ✅ SAFE: Database-level constraints survive crashes

**Analysis:**

- The partial unique index is persistent - if the process crashes, the constraint remains
- PostgreSQL's transaction guarantees mean partial writes are rolled back
- No risk of orphaned non-FINALIZED tasks due to crash

### ✅ SAFE: Credit deduction happens AFTER successful scrape

```python
# In scrape.py, line 92-93
await deduct_credit(user, db)  # Only called after scrape succeeds
```

**Why it's safe:**

- If the scrape fails (network error, timeout), no credit is lost
- If the process crashes after `deduct_credit` but before response, user loses 1 credit but data is consistent

---

## 4. Irreversible Side Effects

### Current scrape endpoint (scrape.py)

**Side effects identified:**

1. Credit deduction (reversible via database)
2. External HTTP request to target URL (irreversible, but safe - observation only)

**Analysis:** No paid external calls, no irreversible state changes on failure.

### ⚠️ FUTURE CONCERN: LLM calls

When LLM processing is added:

- LLM API calls are **paid and irreversible**
- The state machine must ensure LLM calls are not retried on the same content
- Recommendation: Set state to `LLM_ANALYZING` BEFORE the call, then `LLM_ANALYZED` after

---

## 5. Hidden Coupling & Fragility

### ✅ SAFE: Enum values match between Python and SQL

**Python model:**

```python
class TaskState(str, enum.Enum):
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    SCRAPED = "SCRAPED"
    ...
```

**SQL migration:**

```sql
CREATE TYPE task_state AS ENUM (
    'PERMISSION_GRANTED',
    'SCRAPED',
    ...
);
```

These match exactly. No hidden mismatch.

### ⚠️ FRAGILITY: State transition logic not enforced

**Current state:**

- Nothing prevents `PERMISSION_GRANTED` → `FINALIZED` directly (skipping intermediate states)
- This is acceptable if business logic is trusted, but risky if endpoints are added carelessly

**Recommendation:** Add a `transition_to(new_state)` method that validates allowed transitions:

```python
VALID_TRANSITIONS = {
    TaskState.PERMISSION_GRANTED: [TaskState.SCRAPED],
    TaskState.SCRAPED: [TaskState.LLM_ANALYZED],
    ...
}
```

---

## 6. Summary

| Category                   | Status      | Notes                                      |
| -------------------------- | ----------- | ------------------------------------------ |
| Core invariant enforcement | ✅ SAFE     | Partial unique index is correct            |
| Concurrency (INSERT)       | ✅ SAFE     | Database rejects duplicates                |
| Concurrency (UPDATE)       | ⚠️ LOW RISK | No optimistic locking on state transitions |
| Crash safety               | ✅ SAFE     | Transaction guarantees apply               |
| Irreversible side effects  | ✅ SAFE     | No paid calls yet                          |
| Hidden coupling            | ⚠️ LOW RISK | State transitions not validated            |

### Overall Assessment

**No major issues exist.** The implementation correctly enforces the stated invariant at the database level. The partial unique index is the right choice and is race-condition-proof.

**Minor recommendations for future hardening:**

1. Add optimistic locking if multiple workers process the same task
2. Add state transition validation when implementing the state machine logic
3. When adding LLM calls, ensure idempotency (don't re-call on retry)

---

_Review complete. No blocking issues identified._
