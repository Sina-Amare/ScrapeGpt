import { JobListItem, JobResponse, JobState } from "../types";

export const TERMINAL_JOB_STATES = new Set<JobState>([
  "AWAITING_SETUP",
  "ANALYSIS_READY",
  "FAILED",
  "CANCELED",
]);

export const ACTIVE_JOB_STATES = new Set<JobState>(["QUEUED", "ANALYZING"]);

export function isTerminalJob(
  job: JobResponse | JobListItem | null | undefined
): boolean {
  return Boolean(job && TERMINAL_JOB_STATES.has(job.state));
}

export function shouldPollJob(
  job: JobResponse | JobListItem | null | undefined,
  consecutiveFailures: number
): boolean {
  if (consecutiveFailures >= 3) return false;
  if (!job) return true;
  return !isTerminalJob(job);
}

export function jobStateTone(
  state: JobState
): "success" | "warning" | "danger" | "neutral" | "accent" {
  if (state === "ANALYSIS_READY") return "success";
  if (state === "AWAITING_SETUP") return "accent";
  if (state === "FAILED") return "danger";
  if (state === "CANCELED") return "neutral";
  // QUEUED, ANALYZING — in progress
  return "warning";
}

export function jobStateLabel(state: JobState): string {
  if (state === "QUEUED") return "Queued";
  if (state === "ANALYZING") return "Analyzing…";
  if (state === "AWAITING_SETUP") return "Needs review";
  if (state === "ANALYSIS_READY") return "Ready";
  if (state === "FAILED") return "Failed";
  if (state === "CANCELED") return "Canceled";
  return state;
}
