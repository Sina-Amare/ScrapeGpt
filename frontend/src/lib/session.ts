// Tracks auth-session boundaries so stale background work does not surface UI
// for a session that has ended or changed.
//
// Mutation completion handlers live on the global MutationCache (see App.tsx),
// so a provider test that resolves *after* the user logs out — or after a
// different user logs in — would otherwise toast the previous session's result.
// We can't rely on clearing the cache: an in-flight TanStack mutation still
// invokes the cache's onSuccess/onError when it resolves. Instead we record when
// the auth session last changed and drop any notification for a mutation that
// was submitted before that boundary.

let lastAuthChangeAt = 0;

/**
 * Record an auth-session boundary (login or logout). Token refresh does NOT call
 * this, so notifications for in-flight work survive a same-session refresh.
 */
export function markAuthChanged(now: number = Date.now()): void {
  lastAuthChangeAt = now;
}

/** The timestamp of the last login/logout. Exposed for tests. */
export function getLastAuthChangeAt(): number {
  return lastAuthChangeAt;
}

/**
 * Whether a mutation belongs to the current auth session and may surface UI.
 * A mutation submitted before the last auth change is stale (the user logged
 * out, or a different user logged in) and must be suppressed. `submittedAt`
 * comes from TanStack's mutation state; when unknown we are permissive.
 */
export function isCurrentSessionMutation(submittedAt: number | undefined): boolean {
  if (submittedAt === undefined) return true;
  return submittedAt >= lastAuthChangeAt;
}
