import { Download, Eye } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import {
  hasSelectedFields,
  previewFresh,
  RAIL_ORDER,
  RAIL_STEPS,
  railIndex,
  StepKey,
} from "../../lib/workspaceSteps";
import type { ProjectResponse } from "../../types";
import { NextStepBar } from "../ui/NextStepBar";
import { StepRail } from "../ui/StepRail";
import { StepAnalyzing } from "./steps/StepAnalyzing";
import { StepCheck } from "./steps/StepCheck";
import { StepExtract } from "./steps/StepExtract";
import { StepFields } from "./steps/StepFields";
import { StepReview } from "./steps/StepReview";
import { StepScope } from "./steps/StepScope";
import { useWorkspaceMutations } from "./useWorkspaceMutations";
import { useWorkspaceStep } from "./useWorkspaceStep";

const STEP_LABEL: Record<string, string> = {
  review: "Review",
  fields: "Fields",
  scope: "Scope",
  check: "Check",
  extract: "Extract",
};

export function WorkspaceWizard({ project }: { project: ProjectResponse }) {
  const ws = useWorkspaceMutations(project);
  const { current, setCurrent } = useWorkspaceStep(project);

  const isFailed = project.system_state === "FAILED" || project.system_state === "CANCELED";
  const fieldsReady = hasSelectedFields(ws.fields);
  const scopeOk = !ws.scopeNeedsConfirmation;
  const previewOk = previewFresh(project);

  const currentIndex = railIndex(current);

  function goTo(step: StepKey) {
    setCurrent(step);
  }

  async function advanceFromFields() {
    try {
      await ws.saveSpec.mutateAsync();
      setCurrent("scope");
    } catch {
      // saveSpec.error renders in the Fields step; stay put.
    }
  }

  async function advanceFromScope() {
    try {
      await ws.saveScopeAndContinue();
      setCurrent("check");
    } catch {
      // Scope/network errors are surfaced in the Scope step; stay put.
    }
  }

  // One-time "resuming where you left off" hint when we land past Review.
  const resumeNotified = useRef(false);
  useEffect(() => {
    if (resumeNotified.current) return;
    resumeNotified.current = true;
    if (current === "check" || current === "extract") {
      toast.info(`Resuming at: ${STEP_LABEL[current] ?? current}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function renderStep() {
    switch (current) {
      case "analyzing":
        return <StepAnalyzing project={project} />;
      case "review":
        return <StepReview project={project} ws={ws} />;
      case "fields":
        return <StepFields project={project} ws={ws} />;
      case "scope":
        return <StepScope project={project} ws={ws} />;
      case "check":
        return <StepCheck project={project} ws={ws} onAdjustFields={() => goTo("fields")} />;
      case "extract":
        return <StepExtract project={project} ws={ws} />;
      default:
        return null;
    }
  }

  function renderNextBar() {
    switch (current) {
      case "review":
        if (isFailed) return null;
        return (
          <NextStepBar
            primary={{
              label: "Choose what to extract →",
              onClick: () => setCurrent("fields"),
            }}
          />
        );
      case "fields":
        return (
          <NextStepBar
            back={{ onClick: () => setCurrent("review"), disabled: ws.isActive }}
            hint={!fieldsReady ? "Select at least one field to continue" : null}
            primary={{
              label: "Set crawl scope →",
              onClick: advanceFromFields,
              loading: ws.saveSpec.isPending,
              disabled: !fieldsReady || ws.isActive,
            }}
          />
        );
      case "scope":
        return (
          <NextStepBar
            back={{ onClick: () => setCurrent("fields"), disabled: ws.isActive }}
            primary={{
              label: "Check sample data →",
              onClick: advanceFromScope,
              loading: ws.isSavingScope,
              disabled: ws.isActive,
            }}
          />
        );
      case "check":
        if (previewOk) {
          return (
            <NextStepBar
              back={{ onClick: () => setCurrent("scope"), disabled: ws.isActive }}
              hint={!scopeOk ? "Confirm the crawl scope to continue" : null}
              primary={{
                label: "Extract everything →",
                icon: <Download className="h-4 w-4" />,
                onClick: () => {
                  ws.setExtractGateError(null);
                  ws.extractMutation.mutate(false);
                },
                loading: ws.isExtracting || ws.extractMutation.isPending,
                disabled: !scopeOk || ws.isActive,
              }}
            />
          );
        }
        return (
          <NextStepBar
            back={{ onClick: () => setCurrent("scope"), disabled: ws.isActive }}
            primary={{
              label: "Run sample preview",
              icon: <Eye className="h-4 w-4" />,
              onClick: () => ws.previewMutation.mutate(),
              loading: ws.previewMutation.isPending,
            }}
          />
        );
      default:
        return null;
    }
  }

  return (
    <div className="grid gap-6">
      <div className="sticky top-16 z-20 -mx-4 border-b border-line bg-surface px-4 py-3 md:-mx-8 md:px-8">
        <StepRail
          steps={RAIL_STEPS}
          currentIndex={currentIndex}
          completedThroughIndex={ws.isCompleted ? RAIL_STEPS.length - 1 : undefined}
          locked={ws.isActive}
          onStepClick={(index) => goTo(RAIL_ORDER[index])}
        />
      </div>

      <AnimatePresence mode="wait">
        <motion.div
          key={current}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
        >
          {renderStep()}
        </motion.div>
      </AnimatePresence>

      {renderNextBar()}
    </div>
  );
}
