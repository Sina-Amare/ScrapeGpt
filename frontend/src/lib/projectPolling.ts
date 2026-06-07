import { ProjectListItem, ProjectResponse, ProjectState } from "../types";

export const TERMINAL_PROJECT_STATES = new Set<ProjectState>([
  "AWAITING_SETUP",
  "ANALYSIS_READY",
  "PREVIEW_READY",
  "COMPLETED",
  "FAILED",
  "CANCELED"
]);

export const ACTIVE_PROJECT_STATES = new Set<ProjectState>([
  "QUEUED",
  "ANALYZING",
  "PREVIEWING",
  "DISCOVERING",
  "EXTRACTING",
  "EXPORTING",
  "PAUSED"
]);

export function shouldPollProject(
  project: ProjectResponse | ProjectListItem | null | undefined,
  consecutiveFailures: number
): boolean {
  if (consecutiveFailures >= 3) return false;
  if (!project) return true;
  return ACTIVE_PROJECT_STATES.has(project.system_state);
}

export function projectTone(
  project: ProjectResponse | ProjectListItem
): "success" | "warning" | "danger" | "neutral" | "accent" {
  if (project.product_status_tone === "success") return "success";
  if (project.product_status_tone === "danger") return "danger";
  if (project.product_status_tone === "warning") return "warning";
  if (project.product_status === "preview_ready") return "accent";
  return "neutral";
}
