import { AlertCircle } from "lucide-react";
import { scopeModeLabel } from "../../../lib/scopeCopy";
import type { CrawlScopeMode, ProjectResponse } from "../../../types";
import { FrontierPreviewPanel } from "../../project/FrontierPreviewPanel";
import { ScopeSelector } from "../../project/ScopeSelector";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
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
    savePatternsMutation,
    frontierPreviewMutation,
    broadenScopeMutation,
    handleModeChange,
    handleConfirmScope,
    scopeChangedAfterPreview,
    pageLimit,
    setPageLimit,
  } = ws;

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
        {draftMode !== null && draftMode !== savedScope?.mode && draftMode !== "CURRENT_PAGE" ? (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <AlertCircle className="h-4 w-4 text-warning" />
            <span className="text-sm text-muted">
              Scope mode changed to <strong>{scopeModeLabel(draftMode)}</strong>. Save to confirm.
            </span>
            <Button
              variant="secondary"
              disabled={saveScopeMutation.isPending}
              onClick={() => saveScopeMutation.mutate(draftMode as CrawlScopeMode)}
            >
              {saveScopeMutation.isPending ? "Saving..." : "Save scope"}
            </Button>
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
