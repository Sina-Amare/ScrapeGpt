# 03 — Frontend v0: Task History, Key Reveal, Capability Panel

## Purpose

Phase 0.5 shipped a working backend: BYOK provider management, encrypted API key storage, LiteLLM connectivity testing, and a scrape pipeline. This document covers the frontend additions that make all of that visible and usable — the "showcase layer" that lets you demonstrate what Phase 0.5 actually built without opening Swagger.

---

## Problem / Purpose

Three things were working in the backend but invisible in the UI:

1. **Task history** — completed scrape tasks existed in the database but `GET /scrape/tasks` had no frontend caller, so the dashboard only showed the one active task. Past runs were invisible.
2. **Provider API key** — keys were encrypted at rest and normal provider responses never returned them. Users still needed an explicit way to verify what was stored, so reveal became a password-confirmed sensitive action.
3. **Test result detail** — `POST /providers/{id}/test` returned `capability_flags` (connectivity, validated_json, native_json, error_type, error_detail) but the UI only showed "Provider test succeeded." or a plain error string. The structured flags were discarded.

---

## What Was Built

### Backend additions (`app/api/v1/endpoints/scrape.py`)

Added `created_at: datetime | None = None` to `TaskResponse`. All four response construction sites in the file now include `created_at=t.created_at` (the field was already present on `ScrapeTask`). The `start_scrape` response omits it (leaves as `None`) since the created-at isn't meaningful in a 202 response.

### Frontend type updates (`frontend/src/types.ts`)

- `TaskResponse`: added `created_at: string | null`
- New `ProviderKeyResponse = { api_key: string }`

### API client (`frontend/src/lib/api.ts`)

Two new methods on the `api` object:
- `listTasks()` → `GET /api/v1/scrape/tasks` — returns `TaskResponse[]`
- `revealProviderKey(id, { password })` → `POST /api/v1/providers/{id}/reveal-key` — returns `ProviderKeyResponse` after password confirmation

Both imported `ProviderKeyResponse` from `../types`.

### Dashboard task history (`frontend/src/pages/DashboardPage.tsx`)

Added a second `useQuery` (`history`) alongside the existing `task` query. The history query is fire-and-forget (no polling — history doesn't change between refreshes) and shows up as a table below the active task panel with columns: `#` (task_id), URL (truncated to 52 chars, full URL in `title` attribute), State (Badge with `stateTone`), Date (locale-formatted using `formatDate`).

Error rows show the error message in red text below the URL. The Refresh button now calls `history.refetch()` alongside `task.refetch()`.

**Design decision:** history query does not poll. Only the active task polls at 2s. Polling history every 2s would be wasteful — a completed task's record doesn't change.

### ProvidersPage rewrites (`frontend/src/pages/ProvidersPage.tsx`)

**`ApiKeyField` component**: A drop-in replacement for `<Input type="password">` in the provider form. Uses a relative-positioned `<div>` with a plain `<input>` and an Eye/EyeOff toggle button (same pattern as `PasswordField` in AuthPages). The `tabIndex={-1}` on the toggle keeps keyboard flow through the form natural. On create, the placeholder is empty (key is required). On edit, the placeholder reads "Leave blank to keep existing key".

**`CapFlag` component**: Renders a single capability row — `CheckCircle2` (green) or `XCircle` (red) plus a label. Used inside `CapabilityPanel`.

**`CapabilityPanel` component**: Shown inline below the alert area after a test completes. Shows three flags (Connectivity, JSON validated, Native JSON mode), a green success note on pass, or the error type + message from `capability_flags.error_type` / `result.error` on failure. Has an X button to dismiss. **Not a modal** — inline panel so the table remains visible.

**`ConfirmRevealDialog` component**: A password confirmation modal shown before decrypting a stored provider key. It calls the reveal endpoint only after the password form is submitted.

**`RevealKeyDialog` component**: A `Dialog` wrapping a read-only monospace input showing the decrypted key, plus a Copy button that uses `navigator.clipboard.writeText` and shows a `Check` icon for 2 seconds after copy. Note: clipboard access requires HTTPS or localhost — works correctly in dev.

**State changes in `ProvidersPage`:**
- Removed `testResult: string | null` (plain string was insufficient)
- Added `lastTest: { name: string; result: ProviderTestResponse } | null`
- Added `revealPrompt: { id: number; name: string } | null`
- Added `revealDialog: { id: number; name: string; key: string } | null`
- Added `reveal` mutation (calls `api.revealProviderKey` with password, then sets `revealDialog`)
- `test` mutation now stores the full `ProviderTestResponse` in `lastTest`, with the provider name resolved from `providers.data`

**Action buttons per row:** Test, Reveal, Edit, Delete — in that order. Reveal opens the password confirmation dialog before any decrypt call.

---

## Invariants Enforced

- **API key never in table rows.** The key is only loaded into state after explicit password-confirmed reveal and lives only in the `revealDialog` state — not in the provider list query cache. Closing the dialog drops it.
- **Reveal endpoint is owner- and password-scoped.** `POST /providers/{id}/reveal-key` calls `_get_owned_provider_or_404` and verifies the current account password before decrypting. A user cannot reveal another user's key.
- `created_at` is `datetime | None` on the Pydantic schema — the `start_scrape` 202 response omits it without error.
- History table re-uses `stateTone` from `taskPolling.ts` — COMPLETED → success (green), FAILED → danger (red), others → neutral/warning. Consistent with the active task badge.

---

## Design Decisions & Rejected Alternatives

**Inline capability panel vs. modal**: chose inline because the test result relates directly to the provider row the user just clicked — a modal would hide the context. The dismissible panel below alerts keeps the table in view.

**Per-provider test state vs. global**: `lastTest` stores one result at a time. If you test two providers quickly, the second overwrites the first. This is fine for the showcase — the user is testing one provider at a time. An alternative would be to store test results per `provider.id` inside the provider list data — but that requires merging server state with local UI state and is over-engineered for Phase 0.5.

**Reveal key in form vs. separate dialog**: chose separate "Reveal" button (not prefilling the edit form). Prefilling would require fetching the key every time the edit dialog opens, which is wasteful and exposes the key to users who open the edit dialog for other reasons (name change, model update). Explicit reveal is better OPSEC and matches how other tools handle this (Cline shows it in a separate panel, not in the form).

**`created_at` on `start_scrape` response**: intentionally omitted (left as `None`). The field isn't displayed in the 202 response — the task history table shows it, and the history query fetches the persisted row where `created_at` is set by SQLAlchemy's `server_default`.

---

## Runtime Lifecycle

1. User opens Providers page → `listProviders` query fires → table renders
2. User clicks "Test" → `test.mutate(id)` → `POST /providers/{id}/test` → server calls LiteLLM → returns `ProviderTestResponse` with `capability_flags` → `onSuccess` sets `lastTest` → `CapabilityPanel` mounts below alerts
3. User clicks "Reveal" → password confirmation dialog opens → submit password → `POST /providers/{id}/reveal-key` → server verifies ownership and password → decrypts with Fernet → returns `{ api_key }` → `onSuccess` sets `revealDialog` → `RevealKeyDialog` mounts
4. User copies key → `navigator.clipboard.writeText` → "Copied" state for 2s → dialog remains open until user closes
5. User opens Dashboard → `getCurrentTask` + `listTasks` queries fire in parallel → active task section + history table render independently

---

## Concurrency / Crash Analysis

- All mutations are independent — no shared transaction. A failed reveal doesn't affect a pending test.
- `reveal.isPending` disables the Reveal button during the in-flight request, preventing duplicate decrypt calls.
- The `revealDialog` key string is in React state — not persisted to localStorage or the URL. Page refresh drops it. This is intentional.

---

## Safe Evolution Notes

- To add more capability flags (e.g., `function_calling`, `vision`), add them to the backend `test_provider_config` return dict and add a `<CapFlag>` row in `CapabilityPanel`. No schema change needed — `capability_flags` is already `JSONB`.
- To show per-provider test history (not just the last test), change `lastTest` to a `Map<number, ProviderTestResponse>` keyed by provider ID.
- The task history table is capped at 20 rows by the backend (`LIMIT 20`). To add pagination, add `skip: number` param to the backend endpoint and `useInfiniteQuery` on the frontend.
- `created_at` is currently formatted with `toLocaleString` — if the backend returns UTC and the user's locale is different, times display in local timezone as expected. If the backend adds timezone info to the response, no frontend change is needed.
