import { Check } from "lucide-react";
import { motion } from "motion/react";

export type RailStep = { key: string; label: string };

/**
 * Horizontal progress rail for the extraction wizard. `currentIndex` is the
 * active step; steps before it are "done" (and revisitable when `onStepClick` is
 * provided and not `locked`), steps after it are upcoming. Pass `currentIndex = -1`
 * to render the rail with no active step (e.g. during the analyzing overlay).
 */
export function StepRail({
  steps,
  currentIndex,
  completedThroughIndex,
  onStepClick,
  locked,
}: {
  steps: RailStep[];
  currentIndex: number;
  completedThroughIndex?: number;
  onStepClick?: (index: number) => void;
  locked?: boolean;
}) {
  return (
    <div className="flex items-center gap-1 overflow-x-auto scrollbar-none">
      {steps.map((step, i) => {
        const status =
          i < currentIndex || (completedThroughIndex != null && i <= completedThroughIndex)
            ? "done"
            : i === currentIndex
            ? "active"
            : "upcoming";
        const clickable = !locked && !!onStepClick && i <= currentIndex && status !== "active";
        const isLast = i === steps.length - 1;
        return (
          <div key={step.key} className="flex shrink-0 items-center">
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onStepClick?.(i)}
              className={`flex items-center gap-2 rounded-md px-2 py-1.5 transition ${
                clickable ? "cursor-pointer hover:bg-porcelain" : "cursor-default"
              }`}
            >
              <span
                className={`relative flex h-7 w-7 items-center justify-center rounded-full border text-xs font-bold ${
                  status === "done"
                    ? "border-primary bg-primary text-onprimary"
                    : status === "active"
                    ? "border-teal text-teal"
                    : "border-line bg-surface text-muted/60"
                }`}
              >
                {status === "active" ? (
                  <motion.span
                    layoutId="rail-active-ring"
                    className="absolute inset-0 rounded-full ring-2 ring-teal/30"
                    transition={{ type: "spring", stiffness: 500, damping: 40 }}
                  />
                ) : null}
                {status === "done" ? <Check className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <span
                className={`whitespace-nowrap text-sm font-semibold ${
                  status === "active"
                    ? "text-teal"
                    : status === "done"
                    ? "text-ink"
                    : "text-muted/60"
                }`}
              >
                {step.label}
              </span>
            </button>
            {!isLast ? (
              <span
                className={`mx-1 h-px w-5 shrink-0 ${
                  i < currentIndex || (completedThroughIndex != null && i < completedThroughIndex)
                    ? "bg-teal"
                    : "bg-line"
                }`}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
