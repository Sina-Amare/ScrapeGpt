import { useEffect, useRef, useState } from "react";
import type { ProjectResponse } from "../../types";
import { entryStep, forcedStep, StepKey } from "../../lib/workspaceSteps";

/**
 * Owns the wizard's current step. The step is DERIVED from project state, never
 * persisted: the initial value comes from `entryStep`, and `forcedStep` snaps the
 * user to the right place when the project transitions (analysis finishes, an
 * extraction starts, a run fails). Between forced transitions the user navigates
 * freely via the rail / NextStepBar. Re-running this with a fresh `project` (from
 * polling) is safe — it only reacts to `system_state` changes.
 */
export function useWorkspaceStep(project: ProjectResponse) {
  const [current, setCurrent] = useState<StepKey>(() => entryStep(project));
  const prevState = useRef(project.system_state);

  useEffect(() => {
    const state = project.system_state;
    if (state === prevState.current) return;
    prevState.current = state;
    const forced = forcedStep(state);
    if (forced) {
      setCurrent((c) => (c === forced ? c : forced));
    } else {
      // Analysis just finished — leave the analyzing overlay for the flow.
      setCurrent((c) => (c === "analyzing" ? "review" : c));
    }
  }, [project.system_state]);

  return { current, setCurrent };
}
