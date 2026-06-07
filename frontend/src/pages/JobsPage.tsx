import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { Table } from "../components/ui/Table";
import { api } from "../lib/api";
import { jobStateTone, jobStateLabel, TERMINAL_JOB_STATES } from "../lib/jobPolling";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function truncateUrl(url: string, max = 52): string {
  return url.length > max ? url.slice(0, max) + "…" : url;
}

export function JobsPage() {
  const queryClient = useQueryClient();
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(100),
    refetchInterval: 5000,
  });

  const deleteMutation = useMutation({
    mutationFn: api.deleteJob,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setDeleteError(null);
    },
    onError: (err) => {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete job");
    },
  });

  return (
    <>
      <PageHeader title="Analysis Jobs" eyebrow="Phase 1 — Site analysis">
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={() => void jobs.refetch()}
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
          <Link to="/jobs/new">
            <Button>
              <Plus className="h-4 w-4" />
              New job
            </Button>
          </Link>
        </div>
      </PageHeader>

      {deleteError ? (
        <Alert tone="danger" >{deleteError}</Alert>
      ) : null}

      {jobs.isLoading ? (
        <div className="grid gap-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : jobs.error ? (
        <Alert tone="danger">Could not load jobs.</Alert>
      ) : !jobs.data?.length ? (
        <div className="rounded-xl border border-line bg-surface p-12 text-center shadow-panel">
          <p className="text-sm text-muted">
            No analysis jobs yet.{" "}
            <Link to="/jobs/new" className="font-semibold text-teal hover:text-teal-dark">
              Start your first one →
            </Link>
          </p>
        </div>
      ) : (
        <Table headings={["#", "URL", "Mode", "State", "Confidence", "Date", "Actions"]}>
          {jobs.data.map((j) => (
            <tr key={j.id} className="transition-colors hover:bg-teal-soft/40">
              <td className="px-4 py-3 font-mono text-sm font-semibold text-ink">
                {j.id}
              </td>
              <td className="max-w-xs px-4 py-3">
                <span className="block truncate text-sm text-muted" title={j.url}>
                  {truncateUrl(j.url)}
                </span>
                {j.error ? (
                  <span
                    className="mt-0.5 block truncate text-xs text-red-500"
                    title={j.error}
                  >
                    {j.error}
                  </span>
                ) : null}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-muted">
                {j.extraction_mode}
              </td>
              <td className="px-4 py-3">
                <Badge tone={jobStateTone(j.state)}>{jobStateLabel(j.state)}</Badge>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                {j.confidence != null
                  ? `${(j.confidence * 100).toFixed(0)}%`
                  : "—"}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                {formatDate(j.created_at)}
              </td>
              <td className="whitespace-nowrap px-4 py-3">
                <div className="flex gap-2">
                  <Link to={`/jobs/${j.id}`}>
                    <button
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-muted hover:border-teal hover:text-teal hover:bg-teal-soft transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-teal"
                      title="View details"
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                  </Link>
                  <button
                    onClick={() => deleteMutation.mutate(j.id)}
                    disabled={!TERMINAL_JOB_STATES.has(j.state) || deleteMutation.isPending}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-red-500/70 hover:border-danger hover:text-danger hover:bg-red-50 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-danger disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-surface disabled:hover:border-line disabled:hover:text-red-500/50"
                    title="Delete job"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}
