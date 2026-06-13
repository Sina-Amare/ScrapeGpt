import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  getLastAuthChangeAt,
  isCurrentSessionMutation,
  markAuthChanged,
} from "./session";

// ---------------------------------------------------------------------------
// session — auth-session boundary guard for mutation notifications
// ---------------------------------------------------------------------------

describe("session — isCurrentSessionMutation", () => {
  it("is permissive when submittedAt is unknown", () => {
    markAuthChanged(1_000);
    assert.equal(isCurrentSessionMutation(undefined), true);
  });

  it("delivers a mutation submitted after the last auth change", () => {
    markAuthChanged(1_000);
    assert.equal(isCurrentSessionMutation(2_000), true);
  });

  it("delivers a mutation submitted exactly at the auth-change boundary", () => {
    markAuthChanged(1_000);
    assert.equal(isCurrentSessionMutation(1_000), true);
  });

  it("suppresses a mutation submitted before a later auth change (logout / account switch)", () => {
    // A test started at t=1000 within session A.
    markAuthChanged(500);
    const submittedAt = 1_000;
    assert.equal(isCurrentSessionMutation(submittedAt), true);

    // Logout (or a different user logging in) happens at t=2000. The earlier
    // test now belongs to a stale session and must not surface UI.
    markAuthChanged(2_000);
    assert.equal(isCurrentSessionMutation(submittedAt), false);
  });

  it("records the most recent auth-change timestamp", () => {
    markAuthChanged(4_242);
    assert.equal(getLastAuthChangeAt(), 4_242);
  });
});
