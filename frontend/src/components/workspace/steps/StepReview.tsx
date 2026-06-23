import { Link } from "react-router-dom";
import { canRetryWithProvider, errorHelp } from "../../../lib/errorHelp";
import { projectTone } from "../../../lib/projectPolling";
import type { ProjectResponse } from "../../../types";
import { Alert } from "../../ui/Alert";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { ProviderSelect } from "../../ui/ProviderSelect";
import { Select } from "../../ui/Select";
import type { WorkspaceController } from "../useWorkspaceMutations";
import { ConfidenceBar, StepCard } from "./shared";

export function StepReview({
  project,
  ws,
}: {
  project: ProjectResponse;
  ws: WorkspaceController;
}) {
  const {
    retryMutation,
    siblingMutation,
    setSessionMutation,
    sessions,
    retryProviderId,
    setRetryProviderId,
    showDeveloper,
    setShowDeveloper,
  } = ws;

  const rawAlt = project.fetch_metadata?.alternate_mode_suggestion;
  const altMode = rawAlt === "CONTENT" ? "CONTENT" : rawAlt === "STRUCTURED" ? "STRUCTURED" : null;

  return (
    <div className="grid gap-6">
      <StepCard title="">
        <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <h2 className="break-all text-xl font-bold text-ink">{project.url}</h2>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge tone={projectTone(project)}>{project.product_status_label}</Badge>
              <Badge tone="neutral">{project.detected_type ?? project.extraction_mode}</Badge>
              <Badge tone="neutral">{project.selected_field_count} selected fields</Badge>
            </div>
          </div>
          <div className="w-full rounded-lg border border-line bg-porcelain p-4 sm:w-72">
            <p className="mb-2 text-xs font-bold uppercase tracking-widest text-muted">Confidence</p>
            <ConfidenceBar value={project.confidence} />
          </div>
        </div>

        {altMode ? (
          <div className="mt-5">
            <Alert tone="info">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <span>
                  This page also looks like it has{" "}
                  <strong>
                    {altMode === "CONTENT"
                      ? "article / document content"
                      : "structured, table-like data"}
                  </strong>
                  . Extract that too — it starts a separate project, so you keep this one.
                </span>
                <Button
                  variant="secondary"
                  className="shrink-0"
                  onClick={() => siblingMutation.mutate({ url: project.url, mode: altMode })}
                  disabled={siblingMutation.isPending}
                >
                  {siblingMutation.isPending
                    ? "Starting…"
                    : `Also extract as ${altMode === "CONTENT" ? "Content" : "Structured"}`}
                </Button>
              </div>
            </Alert>
          </div>
        ) : null}

        {project.error ? (
          <div className="mt-5 flex flex-col gap-2">
            {(() => {
              const help = errorHelp(project.error_code);
              const providerRetryable =
                project.system_state === "FAILED" && canRetryWithProvider(Boolean(project.analysis));
              return (
                <Alert tone="danger">
                  <div className="flex flex-col gap-3">
                    <div>
                      <p className="font-semibold">{help.title}</p>
                      <p className="mt-0.5 text-sm">{help.guidance}</p>
                      {project.error && project.error !== help.guidance ? (
                        <p className="mt-1 text-xs opacity-70">Details: {project.error}</p>
                      ) : null}
                      {project.analysis ? (
                        <p className="mt-1 text-xs opacity-80">
                          Your analysis is kept — retry continues from field setup without
                          re-analyzing.
                        </p>
                      ) : null}
                    </div>
                    {project.system_state === "FAILED" ? (
                      <div className="flex flex-wrap items-center gap-2">
                        {providerRetryable ? (
                          <>
                            <span className="text-sm">Retry with provider:</span>
                            <ProviderSelect
                              value={retryProviderId ?? project.provider_config_id}
                              onChange={setRetryProviderId}
                              className="min-w-[200px]"
                            />
                          </>
                        ) : null}
                        <Button
                          variant="secondary"
                          className="shrink-0"
                          onClick={() =>
                            retryMutation.mutate(
                              providerRetryable
                                ? retryProviderId ?? project.provider_config_id ?? undefined
                                : undefined
                            )
                          }
                          disabled={retryMutation.isPending}
                        >
                          {retryMutation.isPending ? "Retrying…" : "Retry"}
                        </Button>
                      </div>
                    ) : null}
                    {retryMutation.error ? (
                      <p className="text-xs text-danger">
                        Retry failed: {retryMutation.error.message}. Please try again.
                      </p>
                    ) : null}
                  </div>
                </Alert>
              );
            })()}
            {project.error_code === "BOT_PROTECTION_BLOCKED" && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm">
                <p className="font-medium text-amber-800">
                  ScrapeGPT cannot pass this bot protection automatically.
                </p>
                {(() => {
                  const domain = (() => {
                    try {
                      return new URL(project.url).hostname;
                    } catch {
                      return "";
                    }
                  })();
                  const matching = (sessions ?? []).filter(
                    (s) => s.is_active && (s.domain === domain || domain.endsWith(`.${s.domain}`))
                  );
                  if (matching.length === 0) {
                    return (
                      <p className="mt-1 text-amber-700">
                        <Link to="/sessions" className="underline">
                          Add a browser session for {domain || "this domain"}
                        </Link>{" "}
                        in Settings → Sessions, then retry.
                      </p>
                    );
                  }
                  return (
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span className="text-amber-700">Use a saved session:</span>
                      <Select
                        value={String(project.browser_session_id ?? "")}
                        onChange={(e) => {
                          const val = e.target.value;
                          setSessionMutation.mutate(val ? Number(val) : null);
                        }}
                        className="text-sm"
                      >
                        <option value="">— Select session —</option>
                        {matching.map((s) => (
                          <option key={s.id} value={String(s.id)}>
                            {s.name}
                          </option>
                        ))}
                      </Select>
                      {setSessionMutation.error && (
                        <span className="text-xs text-red-600">
                          {setSessionMutation.error.message}
                        </span>
                      )}
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        ) : null}

        {project.progress.crawl_pages_blocked > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm font-medium text-warning dark:text-amber-400">
              {project.progress.crawl_pages_blocked} page(s) blocked during extraction
            </summary>
            <ul className="mt-2 space-y-1 rounded-md border border-amber-300/40 bg-amber-500/[0.06] p-3">
              {(project.progress.blocked_pages_detail ?? []).map((p, i) => (
                <li key={i} className="flex flex-wrap gap-2 text-xs text-muted">
                  <span className="font-mono max-w-sm truncate text-ink">{p.url}</span>
                  <span className="text-warning dark:text-amber-400">{p.error ?? p.block_reason}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {project.progress.crawl_pages_failed > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm font-medium text-danger dark:text-red-400">
              {project.progress.crawl_pages_failed} page(s) failed during extraction
            </summary>
            <ul className="mt-2 space-y-1 rounded-md border border-red-300/30 bg-red-500/[0.06] p-3">
              {(project.progress.failed_pages_detail ?? []).map((p, i) => (
                <li key={i} className="flex flex-wrap gap-2 text-xs text-muted">
                  <span className="font-mono max-w-sm truncate text-ink">{p.url}</span>
                  <span className="text-danger dark:text-red-400">{p.error ?? p.block_reason}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted">
              {project.system_state === "FAILED"
                ? "Use Retry above to reopen the project, then start extraction again."
                : "These pages were skipped, but extraction completed with partial results."}
            </p>
          </details>
        )}
        {project.warnings.length ? (
          <div className="mt-5">
            <Alert tone="info">
              <ul className="list-disc space-y-1 pl-4">
                {project.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </Alert>
          </div>
        ) : null}
      </StepCard>

      <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
        <button
          type="button"
          className="text-xs text-muted/70 transition hover:text-muted"
          onClick={() => setShowDeveloper(!showDeveloper)}
        >
          {showDeveloper ? "Hide raw debug data" : "Show raw debug data"}
        </button>
        {showDeveloper ? (
          <>
            <p className="mt-1 text-xs text-muted/60">
              Technical details for debugging or support. Not needed for normal use.
            </p>
            <pre className="mt-4 overflow-x-auto rounded-lg border border-line bg-porcelain p-4 font-mono text-xs text-ink">
              {JSON.stringify(
                {
                  system_state: project.system_state,
                  render_mode: project.render_mode,
                  workflow_mode: project.workflow_mode,
                  fetch_metadata: project.fetch_metadata,
                  analysis: project.analysis,
                  spec: project.spec,
                  frontier_preview: project.frontier_preview,
                },
                null,
                2
              )}
            </pre>
          </>
        ) : null}
      </section>
    </div>
  );
}
