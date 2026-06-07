import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCog,
  ChevronDown,
  ChevronRight,
  Eye,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { StatTile } from "../components/ui/StatTile";
import { Table } from "../components/ui/Table";
import { TaskResultPanel } from "../components/ui/TaskResultPanel";
import { ConfirmDeleteTaskDialog } from "../components/ui/ConfirmDeleteTaskDialog";
import { TaskDetailDialog } from "../components/ui/TaskDetailDialog";
import { ApiError, api } from "../lib/api";
import { jobStateTone, jobStateLabel, shouldPollJob } from "../lib/jobPolling";
import { shouldPollTask, stateTone } from "../lib/taskPolling";
import { JobListItem } from "../types";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
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

// ---------------------------------------------------------------------------
// Analysis Jobs section (primary)
// ---------------------------------------------------------------------------

function AnalysisJobsSection() {
  const queryClient = useQueryClient();

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(20),
    refetchInterval: (query) => {
      const jobs = query.state.data;
      if (!jobs) return 3000;
      const hasActive = jobs.some((j) =>
        shouldPollJob(j as JobListItem, 0)
      );
      return hasActive ? 2000 : false;
    },
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: api.deleteJob,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (err) => {
      alert(err instanceof Error ? err.message : "Failed to delete job");
    },
  });

  const jobs = jobsQuery.data ?? [];
  const activeCount = jobs.filter((j) =>
    ["QUEUED", "ANALYZING"].includes(j.state)
  ).length;
  const readyCount = jobs.filter((j) => j.state === "ANALYSIS_READY").length;

  return (
    <div className="grid gap-6">
      {/* Stat tiles */}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile
          label="Active jobs"
          value={jobsQuery.isLoading ? "…" : String(activeCount)}
          icon={<BrainCog className="h-5 w-5" />}
        />
        <StatTile
          label="Analysis ready"
          value={jobsQuery.isLoading ? "…" : String(readyCount)}
        />
        <StatTile
          label="Total jobs"
          value={jobsQuery.isLoading ? "…" : String(jobs.length)}
        />
      </div>

      {/* Jobs list */}
      <section className="rounded-xl border border-line bg-surface shadow-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="text-xs font-bold uppercase tracking-widest text-muted">
            Recent analysis jobs
          </h2>
          <Button
            variant="secondary"
            onClick={() => void jobsQuery.refetch()}
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {jobsQuery.isLoading ? (
          <div className="grid gap-3 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : jobsQuery.error ? (
          <div className="p-6">
            <Alert tone="danger">Could not load jobs.</Alert>
          </div>
        ) : jobs.length === 0 ? (
          <div className="grid gap-4 py-14 text-center">
            <p className="text-sm text-muted">
              No analysis jobs yet.
            </p>
            <div className="flex justify-center">
              <Link to="/jobs/new">
                <Button>
                  <BrainCog className="h-4 w-4" />
                  Run first analysis
                </Button>
              </Link>
            </div>
          </div>
        ) : (
          <Table headings={["#", "URL", "Mode", "State", "Date", ""]}>
            {jobs.map((j) => (
              <tr
                key={j.id}
                className="transition-colors hover:bg-teal-soft/40"
              >
                <td className="px-4 py-3 font-mono text-sm font-semibold text-ink">
                  {j.id}
                </td>
                <td className="max-w-xs px-4 py-3">
                  <span
                    className="block truncate text-sm text-muted"
                    title={j.url}
                  >
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
                <td className="whitespace-nowrap px-4 py-3">
                  <Badge tone="neutral">{j.extraction_mode}</Badge>
                </td>
                <td className="px-4 py-3">
                  <Badge tone={jobStateTone(j.state)}>
                    {jobStateLabel(j.state)}
                  </Badge>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                  {formatDate(j.created_at)}
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  <div className="flex gap-2">
                    <Link to={`/jobs/${j.id}`}>
                      <button
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-muted hover:border-teal hover:bg-teal-soft hover:text-teal transition focus:outline-none focus-visible:ring-2 focus-visible:ring-teal focus-visible:ring-offset-2"
                        title="View detail"
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                    </Link>
                    <button
                      onClick={() => deleteMutation.mutate(j.id)}
                      disabled={
                        !["AWAITING_SETUP", "ANALYSIS_READY", "FAILED", "CANCELED"].includes(j.state)
                      }
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-red-500/70 hover:border-danger hover:bg-red-50 hover:text-danger transition focus:outline-none focus-visible:ring-2 focus-visible:ring-danger focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-line disabled:hover:bg-surface disabled:hover:text-red-500/50"
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
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Legacy Scrape section (secondary, collapsible)
// ---------------------------------------------------------------------------

function LegacyScrapeSection() {
  const [failureCount, setFailureCount] = useState(0);
  const [currentNotFound, setCurrentNotFound] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const [deleteTaskTarget, setDeleteTaskTarget] = useState<{
    id: number;
    url: string;
  } | null>(null);

  const queryClient = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: api.deleteTask,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["task-history"] });
      void queryClient.invalidateQueries({ queryKey: ["current-task"] });
      setDeleteTaskTarget(null);
    },
    onError: (err) => {
      alert(err instanceof Error ? err.message : "Failed to delete task");
    },
  });

  const task = useQuery({
    queryKey: ["current-task"],
    queryFn: async () => {
      try {
        const response = await api.getCurrentTask();
        setCurrentNotFound(false);
        setFailureCount(0);
        return response;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          setCurrentNotFound(true);
          return null;
        }
        setFailureCount((count) => count + 1);
        throw error;
      }
    },
    refetchInterval: (query) =>
      shouldPollTask(query.state.data, failureCount, currentNotFound)
        ? 2000
        : false,
    retry: false,
  });

  const history = useQuery({
    queryKey: ["task-history"],
    queryFn: api.listTasks,
    retry: false,
  });

  return (
    <div className="grid gap-6">
      {/* Active task */}
      <section className="rounded-xl border border-line bg-surface p-6 shadow-panel">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-xs font-bold uppercase tracking-widest text-muted">
            Active scrape task
          </h3>
          <Link to="/scrape/new">
            <Button variant="secondary">
              <Plus className="h-4 w-4" />
              New scrape
            </Button>
          </Link>
        </div>

        {task.isLoading ? (
          <div className="grid gap-3">
            <Skeleton className="h-8 w-52" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : task.error && failureCount >= 3 ? (
          <Alert tone="danger">
            Task polling paused after repeated failures. Use refresh to try
            again.
          </Alert>
        ) : !task.data ? (
          <p className="py-6 text-center text-sm text-muted">
            No active scrape task.
          </p>
        ) : (
          <div className="grid gap-5">
            <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <h2 className="text-lg font-bold text-ink">
                  Task #{task.data.task_id}
                </h2>
                <p className="mt-0.5 break-all text-sm text-muted">
                  {task.data.url}
                </p>
              </div>
              <Badge tone={stateTone(task.data.state)}>
                {task.data.state}
              </Badge>
            </div>

            {task.data.error ? (
              <Alert tone="danger">{task.data.error}</Alert>
            ) : null}

            {task.data.content_length ? (
              <div className="rounded-lg border border-line bg-porcelain px-4 py-2.5">
                <p className="text-xs font-semibold text-muted">
                  Scraped{" "}
                  <span className="text-ink">
                    {task.data.content_length.toLocaleString()}
                  </span>{" "}
                  characters of page text
                </p>
              </div>
            ) : null}

            {task.data.state === "COMPLETED" && task.data.result ? (
              <div className="rounded-xl border border-line bg-porcelain p-5">
                <p className="mb-4 text-xs font-bold uppercase tracking-widest text-muted">
                  AI Analysis
                </p>
                <TaskResultPanel
                  result={task.data.result}
                  contentLength={task.data.content_length}
                />
              </div>
            ) : task.data.state !== "COMPLETED" &&
              task.data.state !== "FAILED" ? (
              <div className="rounded-xl border border-dashed border-line bg-porcelain p-5 text-center text-sm text-muted">
                Pipeline running — result will appear here when complete.
              </div>
            ) : null}
          </div>
        )}
      </section>

      {/* Task history */}
      <section>
        <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-muted">
          Scrape history
        </h3>

        {history.isLoading ? (
          <div className="grid gap-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : history.error ? (
          <Alert tone="danger">Could not load scrape history.</Alert>
        ) : !history.data?.length ? (
          <div className="rounded-xl border border-line bg-surface p-8 text-center shadow-panel">
            <p className="text-sm text-muted">
              No scrape tasks yet.
            </p>
          </div>
        ) : (
          <Table headings={["#", "URL", "State", "Date", "Actions"]}>
            {history.data.map((t) => (
              <tr
                key={t.task_id}
                className="transition-colors hover:bg-teal-soft/40"
              >
                <td className="px-4 py-3 font-mono text-sm font-semibold text-ink">
                  {t.task_id}
                </td>
                <td className="max-w-xs px-4 py-3">
                  <span
                    className="block truncate text-sm text-muted"
                    title={t.url}
                  >
                    {truncateUrl(t.url)}
                  </span>
                  {t.error ? (
                    <span
                      className="mt-0.5 block truncate text-xs text-red-500"
                      title={t.error}
                    >
                      {t.error}
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-3">
                  <Badge tone={stateTone(t.state)}>{t.state}</Badge>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                  {formatDate(t.created_at)}
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  <div className="flex gap-2">
                    <button
                      onClick={() => setSelectedTaskId(t.task_id)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-muted hover:border-teal hover:bg-teal-soft hover:text-teal transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-teal"
                      title="View Details"
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() =>
                        setDeleteTaskTarget({ id: t.task_id, url: t.url })
                      }
                      disabled={
                        t.state !== "COMPLETED" && t.state !== "FAILED"
                      }
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-red-500/70 hover:border-danger hover:bg-red-50 hover:text-danger transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-danger disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-line disabled:hover:bg-surface disabled:hover:text-red-500/50"
                      title="Delete Task"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </section>

      {selectedTaskId !== null ? (
        <TaskDetailDialog
          taskId={selectedTaskId}
          onClose={() => setSelectedTaskId(null)}
        />
      ) : null}

      {deleteTaskTarget !== null ? (
        <ConfirmDeleteTaskDialog
          taskId={deleteTaskTarget.id}
          url={deleteTaskTarget.url}
          onCancel={() => setDeleteTaskTarget(null)}
          onConfirm={() => deleteMutation.mutate(deleteTaskTarget.id)}
          submitting={deleteMutation.isPending}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function DashboardPage() {
  const [showLegacy, setShowLegacy] = useState(false);

  return (
    <>
      <PageHeader title="Dashboard" eyebrow="Analysis overview">
        <Link to="/jobs/new">
          <Button>
            <BrainCog className="h-4 w-4" />
            New analysis
          </Button>
        </Link>
      </PageHeader>

      <AnalysisJobsSection />

      {/* Legacy scrape — collapsible */}
      <div className="mt-10">
        <button
          type="button"
          onClick={() => setShowLegacy((v) => !v)}
          className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-muted hover:text-ink transition"
        >
          {showLegacy ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
          Legacy scrape
        </button>

        {showLegacy ? (
          <div className="mt-4">
            <LegacyScrapeSection />
          </div>
        ) : null}
      </div>
    </>
  );
}
