import { AlertCircle, Eye } from "lucide-react";
import { apiErrorCode } from "../../../lib/api";
import { errorHelp } from "../../../lib/errorHelp";
import { scopeModeLabel } from "../../../lib/scopeCopy";
import type { ProjectResponse } from "../../../types";
import { Alert } from "../../ui/Alert";
import { EmptyState } from "../../ui/EmptyState";
import type { WorkspaceController } from "../useWorkspaceMutations";
import { RecordsTable, StepCard } from "./shared";

export function StepCheck({
  project,
  ws,
  onAdjustFields,
}: {
  project: ProjectResponse;
  ws: WorkspaceController;
  onAdjustFields: () => void;
}) {
  const {
    previewMutation,
    extractMutation,
    extractScopeError,
    extractGateError,
    scopeNeedsConfirmation,
    effectiveDraftMode,
  } = ws;

  return (
    <StepCard
      title="Check sample data"
      description="Run the extraction on a single page to confirm the values look right before the full run."
    >
      {project.preview_stale ? (
        <div className="mb-4">
          <Alert tone="warning">
            This sample is from an older field or variant setup. Run the sample preview again before
            trusting these rows.
          </Alert>
        </div>
      ) : null}

      {previewMutation.error
        ? (() => {
            const code = apiErrorCode(previewMutation.error);
            const help = code ? errorHelp(code) : null;
            return (
              <div className="mb-4">
                <Alert tone="danger">
                  {help ? (
                    <div>
                      <p className="font-semibold">{help.title}</p>
                      <p className="mt-1 text-sm">{help.guidance}</p>
                    </div>
                  ) : (
                    previewMutation.error.message
                  )}
                </Alert>
              </div>
            );
          })()
        : null}

      {scopeNeedsConfirmation ? (
        <div className="mb-4">
          <Alert tone="info">
            Confirm the crawl scope (<strong>{scopeModeLabel(effectiveDraftMode)}</strong>) on the
            Scope step before extraction can begin.
          </Alert>
        </div>
      ) : null}

      {extractScopeError ? (
        <div className="mb-4">
          <Alert tone="danger">
            <div className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <div>
                <p className="font-semibold">{extractScopeError}</p>
                <p className="mt-1 text-sm">
                  Go back to the Scope step and confirm the crawl scope. Current mode:{" "}
                  <strong>{scopeModeLabel(effectiveDraftMode)}</strong>.
                </p>
              </div>
            </div>
          </Alert>
        </div>
      ) : null}

      {extractGateError ? (
        <div className="mb-4">
          <Alert tone={extractGateError.code === "ZERO_PREVIEW_RECORDS" ? "warning" : "info"}>
            <div className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <div>
                <p>{extractGateError.message}</p>
                {extractGateError.code === "ZERO_PREVIEW_RECORDS" ? (
                  <button
                    type="button"
                    className="mt-2 inline-block text-sm underline hover:no-underline"
                    onClick={onAdjustFields}
                  >
                    Adjust fields
                  </button>
                ) : (
                  <button
                    type="button"
                    className="mt-2 text-sm underline hover:no-underline"
                    onClick={() => extractMutation.mutate(true)}
                    disabled={extractMutation.isPending}
                  >
                    {extractMutation.isPending ? "Extracting…" : "Extract anyway"}
                  </button>
                )}
              </div>
            </div>
          </Alert>
        </div>
      ) : null}

      {project.preview ? (
        <div className="grid gap-4">
          <RecordsTable
            rows={project.preview.sample_records}
            specFields={project.spec?.fields}
            mode={project.extraction_mode}
          />
          {project.preview.warnings.length ? (
            <Alert tone="warning">
              <ul className="list-disc space-y-1 pl-4 text-sm">
                {project.preview.warnings.map((warning, index) => (
                  <li key={index}>{String(warning)}</li>
                ))}
              </ul>
            </Alert>
          ) : null}
          {project.preview.missing_fields.length ? (
            <Alert tone="info">
              {project.preview.missing_fields.length} selected fields had no sample value.
            </Alert>
          ) : null}
        </div>
      ) : (
        <EmptyState
          icon={<Eye className="h-6 w-6" />}
          title="No sample yet"
          hint="Run the sample preview to see real extracted rows from one page before committing to the full crawl."
        />
      )}
    </StepCard>
  );
}
