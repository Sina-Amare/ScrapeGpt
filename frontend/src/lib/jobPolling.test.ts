import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  isTerminalJob,
  jobStateTone,
  shouldPollJob,
  TERMINAL_JOB_STATES,
  ACTIVE_JOB_STATES,
} from "./jobPolling";
import { JobListItem } from "../types";

const baseJob: JobListItem = {
  id: 1,
  url: "https://example.com",
  state: "ANALYZING",
  extraction_mode: "STRUCTURED",
  workflow_mode: "GUIDED",
  render_mode: "AUTO",
  confidence: null,
  warnings: [],
  error: null,
  error_code: null,
  created_at: new Date().toISOString(),
};

describe("jobPolling — terminal/active sets", () => {
  it("TERMINAL_JOB_STATES contains the four terminal states", () => {
    assert.ok(TERMINAL_JOB_STATES.has("AWAITING_SETUP"));
    assert.ok(TERMINAL_JOB_STATES.has("ANALYSIS_READY"));
    assert.ok(TERMINAL_JOB_STATES.has("FAILED"));
    assert.ok(TERMINAL_JOB_STATES.has("CANCELED"));
    assert.equal(TERMINAL_JOB_STATES.size, 4);
  });

  it("ACTIVE_JOB_STATES contains QUEUED and ANALYZING", () => {
    assert.ok(ACTIVE_JOB_STATES.has("QUEUED"));
    assert.ok(ACTIVE_JOB_STATES.has("ANALYZING"));
    assert.equal(ACTIVE_JOB_STATES.size, 2);
  });
});

describe("isTerminalJob", () => {
  it("returns true for terminal states", () => {
    for (const state of ["AWAITING_SETUP", "ANALYSIS_READY", "FAILED", "CANCELED"]) {
      assert.ok(isTerminalJob({ ...baseJob, state }));
    }
  });

  it("returns false for active states", () => {
    assert.equal(isTerminalJob({ ...baseJob, state: "QUEUED" }), false);
    assert.equal(isTerminalJob({ ...baseJob, state: "ANALYZING" }), false);
  });

  it("returns false for null/undefined", () => {
    assert.equal(isTerminalJob(null), false);
    assert.equal(isTerminalJob(undefined), false);
  });
});

describe("shouldPollJob", () => {
  it("polls when job is null (waiting for first response)", () => {
    assert.equal(shouldPollJob(null, 0), true);
  });

  it("stops after three consecutive failures", () => {
    assert.equal(shouldPollJob(baseJob, 3), false);
  });

  it("polls for active states", () => {
    assert.equal(shouldPollJob({ ...baseJob, state: "QUEUED" }, 0), true);
    assert.equal(shouldPollJob({ ...baseJob, state: "ANALYZING" }, 0), true);
  });

  it("stops for terminal states", () => {
    for (const state of ["AWAITING_SETUP", "ANALYSIS_READY", "FAILED", "CANCELED"]) {
      assert.equal(shouldPollJob({ ...baseJob, state }, 0), false);
    }
  });
});

describe("jobStateTone", () => {
  it("ANALYSIS_READY is success", () => {
    assert.equal(jobStateTone("ANALYSIS_READY"), "success");
  });

  it("AWAITING_SETUP is accent (needs attention)", () => {
    assert.equal(jobStateTone("AWAITING_SETUP"), "accent");
  });

  it("FAILED is danger", () => {
    assert.equal(jobStateTone("FAILED"), "danger");
  });

  it("CANCELED is neutral", () => {
    assert.equal(jobStateTone("CANCELED"), "neutral");
  });

  it("active states are warning", () => {
    assert.equal(jobStateTone("QUEUED"), "warning");
    assert.equal(jobStateTone("ANALYZING"), "warning");
  });
});
