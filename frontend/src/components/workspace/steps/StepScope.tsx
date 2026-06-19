import { AlertCircle, CheckCircle2 } from "lucide-react";
import { scopeModeLabel } from "../../../lib/scopeCopy";
import { suggestedIncludePatterns } from "../../../lib/scopeDefaults";
import type { ProjectResponse } from "../../../types";
import { FrontierPreviewPanel } from "../../project/FrontierPreviewPanel";
import { ScopeSelector } from "../../project/ScopeSelector";
import { Alert } from "../../ui/Alert";
import { Input } from "../../ui/Input";
import type { WorkspaceController } from "../useWorkspaceMutations";
import { StepCard } from "./shared";

export function StepScope({
  project,
  ws,
}: {
  project: ProjectResponse;
  ws: WorkspaceController;
}) {
  const {
    effectiveScope,
    draftMode,
    savedScope,
    isActive,
    saveScopeMutation,
    confirmScopeMutation,
    saveScopeAndContinueMutation,
    savePatternsMutation,
    frontierPreviewMutation,
    broadenScopeMutation,
    handleModeChange,
    handleConfirmScope,
    scopeChangedAfterPreview,
    pageLimit,
    setPageLimit,
  } = ws;
  const suggestedPatterns = suggestedIncludePatterns(effectiveScope, draftMode ?? "CURRENT_PAGE");
  const willApplySuggestedPatterns =
    (draftMode === "COLLECTION" || draftMode === "DATASET") &&
    (effectiveScope?.include_patterns ?? []).length === 0 &&
    suggestedPatterns.length > 0;

  return (
    <div className="grid gap-6">
      <StepCard
        title="What to crawl"
        description="Choose how far ScrapeGPT should crawl from this page before extracting."
      >
        {saveScopeMutation.error ? (
          <div className="mb-4">
            <Alert tone="danger">{saveScopeMutation.error.message}</Alert>
          </div>
        ) : null}
        {confirmScopeMutation.error ? (
          <div className="mb-4">
            <Alert tone="danger">{confirmScopeMutation.error.message}</Alert>
          </div>
        ) : null}
        {savePatternsMutation.error ? (
          <div className="mb-4">
            <Alert tone="danger">{savePatternsMutation.error.message}</Alert>
          </div>
        ) : null}
        {saveScopeAndContinueMutation.error ? (
          <div className="mb-4">
            <Alert tone="danger">{saveScopeAndContinueMutation.error.message}</Alert>
          </div>
        ) : null}
        {project.spec ? (
          <ScopeSelector
            crawlScope={effectiveScope}
            disabled={isActive || saveScopeMutation.isPending || confirmScopeMutation.isPending}
            onModeChange={handleModeChange}
            onConfirm={handleConfirmScope}
            patternsSaving={savePatternsMutation.isPending}
            onPatternsChange={(include, exclude) =>
              savePatternsMutation.mutate({ include, exclude })
            }
          />
        ) : (
          <p className="text-sm text-muted">Scope will be available after analysis.</p>
        )}
        {willApplySuggestedPatterns ? (
          <div className="mt-3 rounded-lg border border-teal/40 bg-teal-soft/30 p-3 text-sm text-ink">
            <div className="flex items-start gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-teal" />
              <span>
                ScrapeGPT found matching related pages automatically. It will use{" "}
                <strong>{suggestedPatterns.join(", ")}</strong> when you continue.
              </span>
            </div>
          </div>
        ) : null}
        {draftMode !== null && draftMode !== savedScope?.mode && draftMode !== "CURRENT_PAGE" ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-porcelain p-3">
            <AlertCircle className="h-4 w-4 text-warning" />
            <span className="text-sm text-muted">
              Scope will be saved as <strong>{scopeModeLabel(draftMode)}</strong> when you continue.
            </span>
          </div>
        ) : null}

        <div className="mt-5 grid gap-2 border-t border-line pt-5 sm:max-w-xs">
          <label className="grid gap-1 text-sm font-semibold text-ink">
            Crawl limit
            <Input
              type="number"
              min={1}
              max={5000}
              value={pageLimit}
              disabled={isActive}
              onChange={(event) => setPageLimit(Number(event.target.value))}
            />
            <span className="font-normal text-xs text-muted">
              Maximum pages to crawl within the selected scope.
            </span>
          </label>
        </div>
      </StepCard>

      <StepCard
        title="Pages to crawl"
        description="Optional: preview the exact URLs ScrapeGPT will visit before you start."
      >
        <FrontierPreviewPanel
          preview={project.frontier_preview}
          loading={frontierPreviewMutation.isPending}
          error={
            frontierPreviewMutation.error?.message ?? broadenScopeMutation.error?.message ?? null
          }
          stale={scopeChangedAfterPreview}
          disabled={!project.spec || isActive}
          broadening={broadenScopeMutation.isPending}
          onGenerate={() => frontierPreviewMutation.mutate()}
          onBroaden={(mode, includePatterns) =>
            broadenScopeMutation.mutate({ mode, includePatterns })
          }
        />
      </StepCard>
    </div>
  );
}
