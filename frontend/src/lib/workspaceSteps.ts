import type { FieldSpec, ProjectResponse, ProjectState } from "../types";
import { isUserConfirmed, requiresConfirmation } from "./scopeCopy";

// "analyzing" is a pre-flow overlay (no rail interaction); the other five are the
// navigable wizard steps.
export type StepKey = "analyzing" | "review" | "fields" | "scope" | "check" | "extract";

export type RailStepDef = { key: Exclude<StepKey, "analyzing">; label: string };

export const RAIL_STEPS: RailStepDef[] = [
  { key: "review", label: "Review" },
  { key: "fields", label: "Fields" },
  { key: "scope", label: "Scope" },
  { key: "check", label: "Check" },
  { key: "extract", label: "Extract" },
];

export const RAIL_ORDER: StepKey[] = RAIL_STEPS.map((s) => s.key);

/** Index of a step in the rail, or -1 for the analyzing overlay. */
export function railIndex(step: StepKey): number {
  return RAIL_ORDER.indexOf(step);
}

const EXTRACTION_STATES: ProjectState[] = [
  "DISCOVERING",
  "EXTRACTING",
  "EXPORTING",
  "PAUSED",
  "COMPLETED",
  // A canceled run kept its partial results — show them on the extract step
  // rather than bouncing to Review (only a hard failure needs the retry UI).
  "CANCELED",
];

/**
 * A project state that pins the wizard to a specific step regardless of where the
 * user navigated — analyzing, a terminal/active extraction (results/progress), or
 * a hard failure (back to Review for the error + retry). Returns null when the
 * state leaves the step up to the user.
 */
export function forcedStep(state: ProjectState): StepKey | null {
  if (state === "QUEUED" || state === "ANALYZING") return "analyzing";
  if (state === "FAILED") return "review";
  if (EXTRACTION_STATES.includes(state)) return "extract";
  return null;
}

/** Where to land when first opening / reloading a project (pure fn of project). */
export function entryStep(project: ProjectResponse): StepKey {
  const forced = forcedStep(project.system_state);
  if (forced) return forced;
  if (project.preview && !project.preview_stale) return "check";
  if (project.system_state === "PREVIEWING" || project.system_state === "PREVIEW_READY") {
    return "check";
  }
  return "review";
}

export function hasSelectedFields(fields: FieldSpec[]): boolean {
  return fields.some((f) => f.selected);
}

/** A scope is "confirmed enough" to advance when it needs no confirmation or is user-confirmed. */
export function scopeConfirmed(mode: string | undefined, status: string | undefined): boolean {
  if (!mode) return true;
  if (!requiresConfirmation(mode)) return true;
  return isUserConfirmed(status);
}

/**
 * A sample preview is trustworthy for extraction when it exists and isn't
 * backend-stale (the backend marks it stale when fields/variants change). A scope
 * change does NOT invalidate the per-page sample — that is covered separately by
 * the scope-confirmation gate and the frontier panel's own stale indicator.
 */
export function previewFresh(project: ProjectResponse): boolean {
  return !!project.preview && !project.preview_stale;
}
