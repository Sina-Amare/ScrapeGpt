import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw, XCircle } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { ApiError, api } from "../lib/api";
import {
  jobStateTone,
  jobStateLabel,
  shouldPollJob,
  ACTIVE_JOB_STATES,
} from "../lib/jobPolling";
import {
  ContentAnalysis,
  JobResponse,
  StructuredAnalysis,
  StructuredCandidateField,
} from "../types";
import { useState } from "react";

// ---------------------------------------------------------------------------
// Analysis display components
// ---------------------------------------------------------------------------

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 80
      ? "bg-success"
      : pct >= 60
      ? "bg-warning"
      : "bg-danger";
  return (
    <div className="flex items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 text-right text-sm font-bold text-ink">{pct}%</span>
    </div>
  );
}

function StructuredResult({ data }: { data: StructuredAnalysis }) {
  return (
    <div className="grid gap-6">
      {/* Summary row */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-line bg-porcelain p-4">
          <p className="text-xs font-bold uppercase tracking-widest text-muted">
            Page type
          </p>
          <p className="mt-1 text-lg font-bold text-ink capitalize">
            {data.page_type}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-porcelain p-4">
          <p className="text-xs font-bold uppercase tracking-widest text-muted">
            Estimated pages
          </p>
          <p className="mt-1 text-lg font-bold text-ink">
            {data.estimated_pages ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-porcelain p-4">
          <p className="text-xs font-bold uppercase tracking-widest text-muted">
            Confidence
          </p>
          <div className="mt-2">
            <ConfidenceBar value={data.confidence} />
          </div>
        </div>
      </div>

      {/* Selectors */}
      {data.repeated_item_selector ? (
        <div>
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-muted">
            Repeated item selector
          </p>
          <code className="block rounded-md border border-line bg-porcelain px-3 py-2 font-mono text-sm text-ink">
            {data.repeated_item_selector}
          </code>
        </div>
      ) : null}

      {data.pagination_selector ? (
        <div>
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-muted">
            Pagination selector
          </p>
          <code className="block rounded-md border border-line bg-porcelain px-3 py-2 font-mono text-sm text-ink">
            {data.pagination_selector}
          </code>
        </div>
      ) : null}

      {/* Candidate fields table */}
      {data.candidate_fields.length > 0 ? (
        <div>
          <p className="mb-3 text-xs font-bold uppercase tracking-widest text-muted">
            Candidate fields ({data.candidate_fields.length})
          </p>
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
                  <th className="px-4 py-2.5">Field</th>
                  <th className="px-4 py-2.5">Selector</th>
                  <th className="px-4 py-2.5">Type</th>
                  <th className="px-4 py-2.5">Confidence</th>
                  <th className="px-4 py-2.5">Samples</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-surface">
                {data.candidate_fields.map((field: StructuredCandidateField) => (
                  <tr key={field.name} className="hover:bg-teal-soft/30 transition-colors">
                    <td className="px-4 py-3">
                      <span className="font-semibold text-ink">{field.label}</span>
                      <span className="ml-1.5 font-mono text-xs text-muted">
                        ({field.name})
                      </span>
                      {field.required ? (
                        <span className="ml-1.5 text-xs font-bold text-teal">required</span>
                      ) : null}
                    </td>
                    <td className="px-4 py-3">
                      <code className="rounded bg-porcelain px-1.5 py-0.5 font-mono text-xs text-ink">
                        {field.selector}
                      </code>
                    </td>
                    <td className="px-4 py-3 text-muted">{field.data_type}</td>
                    <td className="px-4 py-3">
                      <span
                        className={
                          field.confidence >= 0.8
                            ? "font-bold text-success"
                            : field.confidence >= 0.6
                            ? "font-bold text-warning"
                            : "font-bold text-danger"
                        }
                      >
                        {(field.confidence * 100).toFixed(0)}%
                      </span>
                    </td>
                    <td className="px-4 py-3 text-muted">
                      {field.sample_values.slice(0, 2).join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {data.warnings.length > 0 ? (
        <Alert tone="info">
          <ul className="list-disc pl-3 space-y-1">
            {data.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Alert>
      ) : null}
    </div>
  );
}

function ContentResult({ data }: { data: ContentAnalysis }) {
  return (
    <div className="grid gap-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-line bg-porcelain p-4">
          <p className="text-xs font-bold uppercase tracking-widest text-muted">
            Content type
          </p>
          <p className="mt-1 text-lg font-bold text-ink capitalize">
            {data.content_type}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-porcelain p-4">
          <p className="text-xs font-bold uppercase tracking-widest text-muted">
            Estimated pages
          </p>
          <p className="mt-1 text-lg font-bold text-ink">
            {data.estimated_pages ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-porcelain p-4">
          <p className="text-xs font-bold uppercase tracking-widest text-muted">
            Confidence
          </p>
          <div className="mt-2">
            <ConfidenceBar value={data.confidence} />
          </div>
        </div>
      </div>

      <div>
        <p className="mb-1 text-xs font-bold uppercase tracking-widest text-muted">
          Primary content selector
        </p>
        <code className="block rounded-md border border-line bg-porcelain px-3 py-2 font-mono text-sm text-ink">
          {data.primary_content_selector}
        </code>
      </div>

      {data.recommended_chunking ? (
        <div>
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-muted">
            Recommended chunking
          </p>
          <p className="text-sm text-ink">{data.recommended_chunking}</p>
        </div>
      ) : null}

      {data.avg_content_length != null ? (
        <div>
          <p className="mb-1 text-xs font-bold uppercase tracking-widest text-muted">
            Avg content length
          </p>
          <p className="text-sm text-ink">
            {data.avg_content_length.toLocaleString()} characters
          </p>
        </div>
      ) : null}

      {data.metadata_fields.length > 0 ? (
        <div>
          <p className="mb-3 text-xs font-bold uppercase tracking-widest text-muted">
            Metadata fields ({data.metadata_fields.length})
          </p>
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
                  <th className="px-4 py-2.5">Field</th>
                  <th className="px-4 py-2.5">Selector</th>
                  <th className="px-4 py-2.5">Confidence</th>
                  <th className="px-4 py-2.5">Samples</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line bg-surface">
                {data.metadata_fields.map((f) => (
                  <tr key={f.name} className="hover:bg-teal-soft/30 transition-colors">
                    <td className="px-4 py-3">
                      <span className="font-semibold text-ink">{f.label}</span>
                    </td>
                    <td className="px-4 py-3">
                      <code className="rounded bg-porcelain px-1.5 py-0.5 font-mono text-xs text-ink">
                        {f.selector}
                      </code>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={
                          f.confidence >= 0.8
                            ? "font-bold text-success"
                            : f.confidence >= 0.6
                            ? "font-bold text-warning"
                            : "font-bold text-danger"
                        }
                      >
                        {(f.confidence * 100).toFixed(0)}%
                      </span>
                    </td>
                    <td className="px-4 py-3 text-muted">
                      {f.sample_values.slice(0, 2).join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {data.warnings.length > 0 ? (
        <Alert tone="info">
          <ul className="list-disc pl-3 space-y-1">
            {data.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Alert>
      ) : null}
    </div>
  );
}

function AnalysisResult({ job }: { job: JobResponse }) {
  if (!job.analysis) return null;

  const isStructured = job.extraction_mode === "STRUCTURED";
  const isContent = job.extraction_mode === "CONTENT";

  if (isStructured && "candidate_fields" in job.analysis) {
    return <StructuredResult data={job.analysis as StructuredAnalysis} />;
  }
  if (isContent && "primary_content_selector" in job.analysis) {
    return <ContentResult data={job.analysis as ContentAnalysis} />;
  }

  // Fallback: raw JSON view
  return (
    <pre className="overflow-x-auto rounded-xl border border-line bg-porcelain p-4 text-xs text-ink">
      {JSON.stringify(job.analysis, null, 2)}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [failureCount, setFailureCount] = useState(0);

  const jobId = Number(id);

  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: async () => {
      try {
        const result = await api.getJob(jobId);
        setFailureCount(0);
        return result;
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          navigate("/jobs", { replace: true });
        }
        setFailureCount((c) => c + 1);
        throw err;
      }
    },
    refetchInterval: (query) =>
      shouldPollJob(query.state.data, failureCount) ? 2000 : false,
    retry: false,
    enabled: !isNaN(jobId),
  });

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelJob(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const job = jobQuery.data;

  return (
    <>
      <PageHeader
        title={job ? `Job #${job.id}` : "Job detail"}
        eyebrow="Analysis result"
      >
        <div className="flex gap-2">
          <Link to="/jobs">
            <Button variant="secondary">
              <ArrowLeft className="h-4 w-4" />
              All jobs
            </Button>
          </Link>
          {job && ACTIVE_JOB_STATES.has(job.state) ? (
            <Button
              variant="secondary"
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
            >
              <XCircle className="h-4 w-4" />
              Cancel
            </Button>
          ) : null}
          <Button
            variant="secondary"
            onClick={() => void jobQuery.refetch()}
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>
      </PageHeader>

      {jobQuery.isLoading ? (
        <div className="grid gap-4">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : jobQuery.error && failureCount >= 3 ? (
        <Alert tone="danger">
          Failed to load job. Use Refresh to try again.
        </Alert>
      ) : !job ? null : (
        <div className="grid gap-6">
          {/* Status card */}
          <section className="rounded-xl border border-line bg-surface p-6 shadow-panel">
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-3">
                  <h2 className="text-xl font-bold text-ink">
                    {job.url.length > 70
                      ? job.url.slice(0, 70) + "…"
                      : job.url}
                  </h2>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Badge tone={jobStateTone(job.state)}>{jobStateLabel(job.state)}</Badge>
                  <Badge tone="neutral">{job.extraction_mode}</Badge>
                  <Badge tone="neutral">{job.workflow_mode}</Badge>
                  <Badge tone="neutral">{job.render_mode}</Badge>
                </div>
              </div>
              {job.confidence != null ? (
                <div className="shrink-0 rounded-lg border border-line bg-porcelain px-4 py-3 text-center">
                  <p className="text-xs font-bold uppercase tracking-widest text-muted">
                    Confidence
                  </p>
                  <p className="text-2xl font-bold text-ink">
                    {(job.confidence * 100).toFixed(0)}%
                  </p>
                </div>
              ) : null}
            </div>

            {cancelMutation.error ? (
              <div className="mt-4">
                <Alert tone="danger">
                  {cancelMutation.error instanceof Error
                    ? cancelMutation.error.message
                    : "Failed to cancel job"}
                </Alert>
              </div>
            ) : null}

            {job.error ? (
              <div className="mt-4">
                <Alert tone="danger">{job.error}</Alert>
              </div>
            ) : null}

            {job.warnings.length > 0 ? (
              <div className="mt-4">
                <Alert tone="info">
                  <p className="mb-1 font-semibold">Warnings:</p>
                  <ul className="list-disc pl-4 space-y-0.5">
                    {job.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </Alert>
              </div>
            ) : null}

            {/* In-progress hint */}
            {ACTIVE_JOB_STATES.has(job.state) ? (
              <div className="mt-4 rounded-xl border border-dashed border-line bg-porcelain p-5 text-center text-sm text-muted">
                {job.state === "QUEUED"
                  ? "Validating URL and checking robots.txt…"
                  : "Fetching page and running AI analysis…"}
              </div>
            ) : null}
          </section>

          {/* Analysis results */}
          {job.analysis ? (
            <section className="rounded-xl border border-line bg-surface p-6 shadow-panel">
              <h2 className="mb-6 text-xs font-bold uppercase tracking-widest text-muted">
                Analysis result
              </h2>
              <AnalysisResult job={job} />
            </section>
          ) : null}

          {/* Fetch metadata */}
          {job.fetch_metadata ? (
            <section className="rounded-xl border border-line bg-surface p-6 shadow-panel">
              <h2 className="mb-4 text-xs font-bold uppercase tracking-widest text-muted">
                Fetch metadata
              </h2>
              <div className="grid gap-3 sm:grid-cols-3">
                {Object.entries(job.fetch_metadata).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-line bg-porcelain p-3">
                    <p className="text-xs font-bold uppercase tracking-widest text-muted">
                      {k.replace(/_/g, " ")}
                    </p>
                    <p className="mt-0.5 text-sm font-semibold text-ink">
                      {String(v)}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      )}
    </>
  );
}
