import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { normalizeInteractionProfile } from "../../../lib/interactionProfile";
import type { InteractionProfile, ProjectResponse } from "../../../types";
import { Alert } from "../../ui/Alert";
import { VariantsControl } from "../VariantsControl";
import type { WorkspaceController } from "../useWorkspaceMutations";
import { FieldEditor, StepCard } from "./shared";

export function StepFields({
  project,
  ws,
}: {
  project: ProjectResponse;
  ws: WorkspaceController;
}) {
  const {
    fields,
    setFields,
    isActive,
    saveSpec,
    detectInteractionsMutation,
    saveInteractionsMutation,
  } = ws;

  const profile = normalizeInteractionProfile(project.spec?.interaction_profile);
  const hasVariantGroups = profile.groups.length > 0;
  const [variantsOpen, setVariantsOpen] = useState(hasVariantGroups);

  return (
    <div className="grid gap-6">
      <StepCard
        title="What to extract"
        description="Choose the columns to keep and fix any labels or types. Your changes save automatically when you continue."
      >
        {saveSpec.error ? (
          <div className="mb-3">
            <Alert tone="danger">{saveSpec.error.message}</Alert>
          </div>
        ) : null}
        <FieldEditor fields={fields} onChange={setFields} disabled={isActive} />
      </StepCard>

      <section className="card-hover rounded-lg border border-line bg-surface shadow-panel">
        <button
          type="button"
          onClick={() => setVariantsOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-2 px-6 py-4 text-left"
        >
          <span>
            <span className="flex items-center gap-2 font-bold text-ink">
              {variantsOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              Page variations
              <span className="text-xs font-normal text-muted">(optional)</span>
            </span>
            <span className="mt-0.5 block pl-6 text-sm text-muted">
              {hasVariantGroups
                ? "This page shows items more than one way — pick which versions to capture."
                : "Capture per-100g/per-serving, metric/imperial and similar toggles as labelled rows."}
            </span>
          </span>
        </button>
        {variantsOpen ? (
          <div className="border-t border-line p-6">
            <VariantsControl
              profile={project.spec?.interaction_profile ?? null}
              disabled={!project.spec || isActive}
              detecting={detectInteractionsMutation.isPending}
              saving={saveInteractionsMutation.isPending}
              detectError={detectInteractionsMutation.error?.message ?? null}
              saveError={saveInteractionsMutation.error?.message ?? null}
              onDetect={() => detectInteractionsMutation.mutate()}
              onSave={(next: InteractionProfile) => saveInteractionsMutation.mutate(next)}
            />
          </div>
        ) : null}
      </section>
    </div>
  );
}
