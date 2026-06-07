import { useQuery } from "@tanstack/react-query";
import { BrainCog, Check, Globe2, RefreshCw, X } from "lucide-react";
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Select } from "../components/ui/Select";
import { ApiError, api } from "../lib/api";
import { jobStateTone, shouldPollJob } from "../lib/jobPolling";
import { ExtractionMode, JobResponse, RenderMode, WorkflowMode } from "../types";

// ---------------------------------------------------------------------------
// Pipeline stages
// ---------------------------------------------------------------------------

const STAGES: { state: string; label: string }[] = [
  { state: "QUEUED", label: "Queued" },
  { state: "ANALYZING", label: "Analyzing" },
  { state: "AWAITING_SETUP", label: "Needs review" },
  { state: "ANALYSIS_READY", label: "Ready" },
];

function PipelineProgress({ state }: { state: string }) {
  if (state === "FAILED") {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm font-semibold text-red-600">
        <X className="h-4 w-4 shrink-0" />
        Analysis failed — see error below
      </div>
    );
  }
  if (state === "CANCELED") {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-line bg-porcelain px-4 py-3 text-sm font-semibold text-muted">
        <X className="h-4 w-4 shrink-0" />
        Job canceled
      </div>
    );
  }

  const terminalIdx = STAGES.findIndex(
    (s) => s.state === "AWAITING_SETUP" || s.state === "ANALYSIS_READY"
  );
  const currentIdx = STAGES.findIndex((s) => s.state === state);
  const effectiveIdx =
    state === "AWAITING_SETUP"
      ? 2
      : state === "ANALYSIS_READY"
      ? 3
      : currentIdx;
  void terminalIdx;

  return (
    <div className="flex items-center">
      {STAGES.map(({ state: s, label }, idx) => {
        const isDone = idx < effectiveIdx;
        const isActive = idx === effectiveIdx;

        return (
          <div key={s} className="flex flex-1 items-center">
            <div className="flex flex-col items-center gap-1">
              <div
                className={[
                  "flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold transition-all",
                  isDone
                    ? "bg-teal text-white"
                    : isActive
                    ? "bg-teal/15 text-teal ring-2 ring-teal animate-pulse"
                    : "bg-gray-100 text-gray-400",
                ].join(" ")}
              >
                {isDone ? <Check className="h-3.5 w-3.5" /> : <span>{idx + 1}</span>}
              </div>
              <span
                className={[
                  "text-xs whitespace-nowrap",
                  isDone || isActive ? "font-semibold text-ink" : "text-muted",
                ].join(" ")}
              >
                {label}
              </span>
            </div>
            {idx < STAGES.length - 1 ? (
              <div
                className={[
                  "mb-5 h-0.5 flex-1 mx-1 transition-all",
                  idx < effectiveIdx ? "bg-teal" : "bg-gray-100",
                ].join(" ")}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

function admissionError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    const detail = error.detail as Record<string, unknown> | null;
    if (
      detail &&
      typeof detail === "object" &&
      "detail" in detail &&
      detail.detail &&
      typeof detail.detail === "object" &&
      "error_code" in detail.detail
    ) {
      const code = (detail.detail as Record<string, string>).error_code;
      if (code === "NO_PROVIDER_CONFIGURED") {
        return "No AI provider configured. Add one in Providers before running an analysis job.";
      }
      if (code === "ACTIVE_JOB_LIMIT_REACHED") {
        return "Active job limit reached. Wait for a running job to finish before starting a new one.";
      }
    }
    return "Could not start job — limit or configuration issue.";
  }
  return error instanceof Error ? error.message : "Could not start analysis job";
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function NewJobPage() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [extractionMode, setExtractionMode] = useState<ExtractionMode>("STRUCTURED");
  const [workflowMode, setWorkflowMode] = useState<WorkflowMode>("GUIDED");
  const [renderMode, setRenderMode] = useState<RenderMode>("AUTO");
  const [providerConfigId, setProviderConfigId] = useState<string>("");
  const [startedJob, setStartedJob] = useState<JobResponse | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failureCount, setFailureCount] = useState(0);

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: api.listProviders,
  });

  const jobStatus = useQuery({
    queryKey: ["job", startedJob?.id],
    enabled: Boolean(startedJob?.id),
    queryFn: async () => {
      if (!startedJob) return null;
      try {
        const response = await api.getJob(startedJob.id);
        setFailureCount(0);
        return response;
      } catch (err) {
        setFailureCount((c) => c + 1);
        throw err;
      }
    },
    refetchInterval: (query) =>
      shouldPollJob(query.state.data ?? startedJob, failureCount) ? 2000 : false,
    retry: false,
  });

  const job = jobStatus.data ?? startedJob;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitError(null);
    setSubmitting(true);
    try {
      const response = await api.createJob({
        url,
        extraction_mode: extractionMode,
        workflow_mode: workflowMode,
        render_mode: renderMode,
        provider_config_id: providerConfigId ? Number(providerConfigId) : null,
      });
      setStartedJob(response);
      setFailureCount(0);
    } catch (err) {
      setSubmitError(admissionError(err));
    } finally {
      setSubmitting(false);
    }
  }

  const isTerminal =
    job?.state === "ANALYSIS_READY" ||
    job?.state === "AWAITING_SETUP" ||
    job?.state === "FAILED" ||
    job?.state === "CANCELED";

  return (
    <>
      <PageHeader title="New Analysis Job" eyebrow="Phase 1 — Site analysis" />

      <div className="grid gap-6 lg:grid-cols-[400px_1fr]">
        {/* Left: Form */}
        <section className="rounded-xl border border-line bg-surface p-6 shadow-panel">
          <div className="mb-5 flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-teal-soft text-teal">
              <BrainCog className="h-5 w-5" />
            </div>
            <div>
              <h2 className="font-bold text-ink">Analyze URL</h2>
              <p className="text-sm text-muted">
                AI analyzes site structure and suggests extraction fields.
              </p>
            </div>
          </div>

          <form className="grid gap-4" onSubmit={onSubmit}>
            {submitError ? <Alert tone="danger">{submitError}</Alert> : null}

            <Field label="URL to analyze">
              <Input
                type="url"
                placeholder="https://example.com/listings"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </Field>

            <Field label="Extraction mode">
              <Select
                value={extractionMode}
                onChange={(e) => setExtractionMode(e.target.value as ExtractionMode)}
              >
                <option value="STRUCTURED">Structured — fields &amp; records</option>
                <option value="CONTENT">Content — articles &amp; documents</option>
              </Select>
            </Field>

            <Field label="Workflow">
              <Select
                value={workflowMode}
                onChange={(e) => setWorkflowMode(e.target.value as WorkflowMode)}
              >
                <option value="GUIDED">Guided — review results before proceeding</option>
                <option value="FAST">Fast — auto-advance if confidence is high</option>
              </Select>
            </Field>

            <Field label="Render mode">
              <Select
                value={renderMode}
                onChange={(e) => setRenderMode(e.target.value as RenderMode)}
              >
                <option value="AUTO">Auto — static first, browser fallback</option>
                <option value="STATIC">Static only — fastest</option>
                <option value="BROWSER">Browser — JavaScript-heavy sites</option>
              </Select>
            </Field>

            <Field
              label="AI provider"
              hint="Leave blank to use your default provider."
            >
              <Select
                value={providerConfigId}
                onChange={(e) => setProviderConfigId(e.target.value)}
              >
                <option value="">Default provider</option>
                {providers.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.provider} / {p.model})
                  </option>
                ))}
              </Select>
            </Field>

            <Button type="submit" disabled={submitting || Boolean(startedJob)}>
              <Globe2 className="h-4 w-4" />
              {submitting ? "Starting…" : startedJob ? "Job started" : "Start analysis"}
            </Button>
          </form>

          {job ? (
            <div className="mt-5 rounded-xl border border-line bg-porcelain p-4 text-sm">
              <p className="mb-3 text-xs font-bold uppercase tracking-widest text-muted">
                Pipeline
              </p>
              <PipelineProgress state={job.state} />
            </div>
          ) : null}
        </section>

        {/* Right: Live status */}
        <section className="rounded-xl border border-line bg-surface p-6 shadow-panel">
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <h2 className="font-bold text-ink">Analysis status</h2>
              <p className="text-sm text-muted">
                {job && !isTerminal
                  ? "Polling every 2 seconds."
                  : "Submit a URL to begin."}
              </p>
            </div>
            {job ? (
              <Button variant="secondary" onClick={() => void jobStatus.refetch()}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
            ) : null}
          </div>

          {!job ? (
            <div className="rounded-xl border border-dashed border-line bg-porcelain p-10 text-center text-sm text-muted">
              Submit a URL to watch the analysis run.
            </div>
          ) : failureCount >= 3 ? (
            <Alert tone="danger">
              Polling paused after repeated failures. Use Refresh to try again.
            </Alert>
          ) : (
            <div className="grid gap-5">
              <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0">
                  <h3 className="text-lg font-bold text-ink">Job #{job.id}</h3>
                  <p className="mt-0.5 break-all text-sm text-muted">{job.url}</p>
                </div>
                <Badge tone={jobStateTone(job.state)}>{job.state}</Badge>
              </div>

              {job.error ? <Alert tone="danger">{job.error}</Alert> : null}

              {!isTerminal ? (
                <div className="rounded-xl border border-dashed border-line bg-porcelain p-6 text-center text-sm text-muted">
                  {job.state === "QUEUED"
                    ? "Validating URL and checking robots.txt…"
                    : job.state === "ANALYZING"
                    ? "Fetching page and running AI analysis…"
                    : "Processing…"}
                </div>
              ) : job.state === "ANALYSIS_READY" || job.state === "AWAITING_SETUP" ? (
                <div className="rounded-xl border border-green-200 bg-green-50 px-5 py-4 text-sm">
                  <p className="font-semibold text-success">
                    {job.state === "ANALYSIS_READY"
                      ? "Analysis complete — high confidence result"
                      : "Analysis complete — review required"}
                  </p>
                  {job.confidence != null ? (
                    <p className="mt-1 text-muted">
                      Confidence:{" "}
                      <span className="font-bold text-ink">
                        {(job.confidence * 100).toFixed(0)}%
                      </span>
                    </p>
                  ) : null}
                  {job.warnings.length > 0 ? (
                    <ul className="mt-2 list-disc pl-4 text-warning">
                      {job.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  ) : null}
                  <div className="mt-4">
                    <Button onClick={() => navigate(`/jobs/${job.id}`)}>
                      View full analysis →
                    </Button>
                  </div>
                </div>
              ) : null}

              <Link
                className="text-sm font-semibold text-teal hover:text-teal-dark"
                to="/jobs"
              >
                View all jobs →
              </Link>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
