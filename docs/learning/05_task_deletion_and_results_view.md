# 05 — Legacy Scrape Task Deletion and Results View

Documentation detailing the implementation and design decisions for adding task deletion and a detailed results viewer modal on the Dashboard page.

## Purpose & Context

- **Problem:** Users could run legacy scrape tasks and view the immediate live status, but once the task was completed or failed, they could not view detailed structured scrape results later from the **Task History** list. Additionally, there was no way to purge old, failed, or unnecessary tasks from the database directly in the UI.
- **Goal:**
  1. Allow users to permanently delete any completed or failed tasks from the database, preventing clutter.
  2. Implement an elegant, interactive details modal to inspect the exact scraped metadata and LLM analysis findings.
- **Invariants Enforced:**
  - Tasks can only be deleted if they are in a terminal state (`COMPLETED` or `FAILED`). Active tasks (`SCRAPING`, `LLM_PROCESSING`, etc.) cannot be deleted to avoid leaving orphan background worker processes or database tracking discrepancies.
  - Users can only delete or view tasks they own.

## Design Decisions

- **On-Demand Detail Fetching:**
  The `GET /scrape/tasks` history list endpoint intentionally defers loading the large `content` block to keep list page loads fast. Therefore, rather than using list-level caching, clicking **View** queries the full task detail endpoint (`GET /scrape/tasks/{id}`) to load the complete results only when requested.
- **Component Separation:**
  Following the React component architecture, the UI dialogs (`ConfirmDeleteTaskDialog` and `TaskDetailDialog`) are split into their own files under `frontend/src/components/ui/` instead of dumping them inline in `DashboardPage.tsx`. This keeps the main dashboard file clean, readable, and highly maintainable.
- **Consistent UI/UX Design System:**
  The Dialogs leverage the custom `<Dialog>`, `<Button>`, and `<Badge>` design system. The delete confirmation incorporates warning-tinted text and a red variant primary button to highlight the permanent nature of the operation.

## Code Walkthrough

### 1. Backend Deletion Integration
The backend `DELETE /scrape/tasks/{task_id}` endpoint in `app/api/v1/endpoints/scrape.py` verifies:
- Task existence and owner ID match.
- The task's `is_terminal` property is true.

### 2. Frontend Components
- `frontend/src/components/ui/ConfirmDeleteTaskDialog.tsx`: A confirmation popup displaying the target URL and asking the user to confirm the deletion action.
- `frontend/src/components/ui/TaskDetailDialog.tsx`: Fetches task details using React Query key `["task-detail", taskId]`. While loading, it mounts `Skeleton` placeholders. Once loaded, it displays:
  - Clickable target URL, formatted creation timestamp, and raw scraped page character count.
  - Tone-coded status badges (`stateTone`).
  - An `<Alert tone="danger">` showing the backend error message if the task failed.
  - The structured AI summary, classification, and key points in the `<TaskResultPanel>` if the task completed successfully.

### 3. Dashboard Integration
`frontend/src/pages/DashboardPage.tsx`:
- Adds a fifth column (`Actions`) to the Task History table.
- Holds `selectedTaskId` and `deleteTaskTarget` state pointers to manage which modals are mounted.
- Uses `@tanstack/react-query`'s `useMutation` to handle `api.deleteTask` async execution. On success, it calls `invalidateQueries` for both `["task-history"]` and `["current-task"]` keys to refresh the table.

## Lifecycle & Flow

### 1. View Detail Flow
```
[User clicks Eye icon]
         |
         v
[setSelectedTaskId(taskId)] -> Mounts <TaskDetailDialog>
                                      |
                                      v
                             [Fetches GET /tasks/{id}]
                                      |
         +----------------------------+----------------------------+
         | (Loading)                  | (Success: COMPLETED)       | (Success: FAILED)
         v                            v                            v
[Show Skeleton bars]       [Mount <TaskResultPanel>]    [Show red failure Alert]
```

### 2. Delete Flow
```
[User clicks Trash icon]
         |
         v
[setDeleteTaskTarget(task)] -> Mounts <ConfirmDeleteTaskDialog>
                                      |
                                      v
                         [User clicks "Delete task"]
                                      |
                                      v
                       [deleteMutation.mutate(taskId)]
                                      |
                                      v
                      [DELETE /scrape/tasks/{taskId}]
                                      |
                                      v
                        [Invalidate task-history query]
                                      |
                                      v
                       [Table refreshes; Modal closes]
```

## Concurrency & Failure Analysis

- **Process Crash Safety:**
  If the frontend or backend crashes mid-deletion, the transaction aborts safely. The task either remains intact or is deleted atomically. Since deletion uses standard SQL delete operations, database state consistency is guaranteed.
- **Race Condition Prevention:**
  The deletion is guarded by checking `is_terminal` before purging. If a user double-submits a delete request, the second call receives a standard `404 Not Found` because the task was already removed, avoiding race condition issues.

## Things to Be Careful About

- **Deferred Load vs. Cache Staling:**
  Be sure to invalidate `["task-history"]` upon task deletion, otherwise the UI will display a stale list row pointing to a deleted ID, resulting in a 404 error if clicked again.
- **Active Task Deletion Block:**
  The trash icon is explicitly disabled for running tasks in the history table (`t.state !== "COMPLETED" && t.state !== "FAILED"`). This prevents users from initiating delete payloads for active scrapes.

## Future Evolution

- **Bulk Deletion:**
  To support deletion of multiple tasks at once, the `Table` could incorporate select checkboxes, submitting a batch delete array (`POST /scrape/tasks/batch-delete`) to the backend.
- **Detailed Crawl Parser View:**
  In future phases, the `TaskDetailDialog` can easily be extended to mount a sub-list or grid displaying the child pages crawled and individual `extracted_records` scraped.

## Summary

The legacy scrape task deletion and detail viewer feature is correctly implemented with user isolation and state checks. The detail viewer queries the deferred content column on demand to maintain list-view performance, and separate UI components keep the frontend codebase maintainable.
