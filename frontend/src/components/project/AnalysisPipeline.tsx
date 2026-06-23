import { Check, Loader2, X } from "lucide-react";
import { motion } from "motion/react";
import type { ProjectState } from "../../types";

type StepStatus = "pending" | "active" | "done" | "error";

type Step = {
  label: string;
  subtitle: string;
};

const STEPS: Step[] = [
  { label: "Fetching page", subtitle: "Downloading and rendering the URL" },
  { label: "AI analyzing", subtitle: "Identifying extractable data and fields" },
  { label: "Spec ready", subtitle: "Fields and selectors generated" },
  { label: "Review & extract", subtitle: "Configure fields and run extraction" },
];

function stepStatuses(state: ProjectState): StepStatus[] {
  switch (state) {
    case "QUEUED":
      return ["active", "pending", "pending", "pending"];
    case "ANALYZING":
      return ["done", "active", "pending", "pending"];
    case "AWAITING_SETUP":
    case "ANALYSIS_READY":
      return ["done", "done", "done", "active"];
    case "PREVIEW_READY":
    case "PREVIEWING":
      return ["done", "done", "done", "active"];
    case "DISCOVERING":
    case "EXTRACTING":
    case "EXPORTING":
      return ["done", "done", "done", "active"];
    case "COMPLETED":
      return ["done", "done", "done", "done"];
    case "FAILED":
    case "CANCELED":
      return ["done", "error", "pending", "pending"];
    default:
      return ["active", "pending", "pending", "pending"];
  }
}

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "done") {
    return (
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-success text-white">
        <Check className="h-4 w-4" />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-danger text-white">
        <X className="h-4 w-4" />
      </div>
    );
  }
  if (status === "active") {
    return (
      <div className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-onprimary">
        <motion.div
          className="absolute inset-0 rounded-full border-2 border-accent"
          animate={{ scale: [1, 1.6, 1], opacity: [0.7, 0, 0.7] }}
          transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
        />
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }
  return (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-line bg-surface text-muted">
      <span className="h-2 w-2 rounded-full bg-line" />
    </div>
  );
}

export function AnalysisPipeline({ state }: { state: ProjectState }) {
  const statuses = stepStatuses(state);

  return (
    <div className="rounded-lg border border-line bg-porcelain p-5">
      <p className="mb-4 text-xs font-bold uppercase tracking-widest text-muted">
        Analysis progress
      </p>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:gap-0">
        {STEPS.map((step, i) => {
          const status = statuses[i];
          const isLast = i === STEPS.length - 1;
          return (
            <div key={step.label} className="flex flex-1 items-start gap-3 sm:flex-col sm:items-center sm:text-center">
              <div className="flex items-center sm:flex-col sm:gap-0">
                <StepIcon status={status} />
                {!isLast && (
                  <div
                    className={`h-px w-8 sm:h-8 sm:w-px mt-0 sm:mt-0 mx-1 sm:mx-auto ${
                      status === "done" ? "bg-success" : "bg-line"
                    }`}
                  />
                )}
              </div>
              <div className="min-w-0 sm:mt-2 sm:px-1">
                <p
                  className={`text-sm font-semibold leading-tight ${
                    status === "active"
                      ? "text-teal"
                      : status === "done"
                      ? "text-ink"
                      : status === "error"
                      ? "text-danger"
                      : "text-muted/60"
                  }`}
                >
                  {step.label}
                </p>
                <p className="mt-0.5 text-xs text-muted/70 leading-snug">
                  {step.subtitle}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
