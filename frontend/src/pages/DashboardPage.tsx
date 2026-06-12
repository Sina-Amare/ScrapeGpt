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
import { motion } from "motion/react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { StatTile } from "../components/ui/StatTile";
import { Table } from "../components/ui/Table";
import { TaskResultPanel } from "../components/ui/TaskResultPanel";
import { ConfirmDeleteTaskDialog } from "../components/ui/ConfirmDeleteTaskDialog";
import { TaskDetailDialog } from "../components/ui/TaskDetailDialog";
import { ApiError, api } from "../lib/api";
import { ACTIVE_PROJECT_STATES, projectTone, shouldPollProject, TERMINAL_PROJECT_STATES } from "../lib/projectPolling";
import { shouldPollTask, stateTone } from "../lib/taskPolling";
import { ProjectEvent } from "../types";

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
// Projects section (primary)
// ---------------------------------------------------------------------------

function ProjectsSection() {
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(20),
    refetchInterval: (query) => {
      const projects = query.state.data;
      if (!projects) return 3000;
      const hasActive = projects.some((project) =>
        shouldPollProject(project, 0)
      );
      return hasActive ? 2000 : false;
    },
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: api.deleteProject,
    onSuccess: () => {
      setDeleteTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Project deleted");
    },
    onError: (err) => {
      setDeleteTarget(null);
      toast.error(err instanceof Error ? err.message : "Failed to delete project");
    },
  });

  const projects = projectsQuery.data ?? [];
  const activeCount = projects.filter((project) =>
    ACTIVE_PROJECT_STATES.has(project.system_state)
  ).length;
  const readyCount = projects.filter((project) =>
    ["AWAITING_SETUP", "ANALYSIS_READY", "PREVIEW_READY"].includes(project.system_state)
  ).length;

  return (
    <div className="grid gap-6">
      {/* Stat tiles */}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile
          label="Active projects"
          value={projectsQuery.isLoading ? "…" : String(activeCount)}
          icon={<BrainCog className="h-5 w-5" />}
        />
        <StatTile
          label="Ready to review"
          value={projectsQuery.isLoading ? "…" : String(readyCount)}
        />
        <StatTile
          label="Total projects"
          value={projectsQuery.isLoading ? "…" : String(projects.length)}
        />
      </div>

      {/* Projects list */}
      <section className="rounded-xl border border-line bg-surface shadow-panel">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="text-xs font-bold uppercase tracking-widest text-muted">
            Recent projects
          </h2>
          <Button
            variant="secondary"
            onClick={() => void projectsQuery.refetch()}
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {projectsQuery.isLoading ? (
          <div className="grid gap-3 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : projectsQuery.error ? (
          <div className="p-6">
            <Alert tone="danger">Could not load projects.</Alert>
          </div>
        ) : projects.length === 0 ? (
          <div className="grid gap-4 py-14 text-center">
            <p className="text-sm text-muted">
              No extraction projects yet.
            </p>
            <div className="flex justify-center">
              <Link to="/projects/new">
                <Button>
                  <BrainCog className="h-4 w-4" />
                  Start first extraction
                </Button>
              </Link>
            </div>
          </div>
        ) : (
          <Table headings={["#", "URL", "Type", "Status", "Date", ""]}>
            {projects.map((project, index) => (
              <motion.tr
                key={project.id}
                className="transition-colors hover:bg-teal-soft/40"
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(index, 7) * 0.04, duration: 0.25 }}
              >
                <td className="px-4 py-3 font-mono text-sm font-semibold text-ink">
                  {project.id}
                </td>
                <td className="max-w-xs px-4 py-3">
                  <span
                    className="block truncate text-sm text-muted"
                    title={project.url}
                  >
                    {truncateUrl(project.url)}
                  </span>
                  {project.error ? (
                    <span
                      className="mt-0.5 block truncate text-xs text-red-500"
                      title={project.error}
                    >
                      {project.error}
                    </span>
                  ) : null}
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  <Badge tone="neutral">{project.detected_type ?? project.extraction_mode}</Badge>
                </td>
                <td className="px-4 py-3">
                  <Badge tone={projectTone(project)}>
                    {project.product_status_label}
                  </Badge>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                  {formatDate(project.last_activity)}
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  <div className="flex gap-2">
                    <Link to={`/projects/${project.id}`}>
                      <button
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-muted hover:border-teal hover:bg-teal-soft hover:text-teal transition focus:outline-none focus-visible:ring-2 focus-visible:ring-teal focus-visible:ring-offset-2"
                        title="View detail"
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                    </Link>
                    <button
                      onClick={() => setDeleteTarget(project.id)}
                      disabled={
                        !TERMINAL_PROJECT_STATES.has(project.system_state)
                      }
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-red-500/70 hover:border-danger hover:bg-red-50 hover:text-danger transition focus:outline-none focus-visible:ring-2 focus-visible:ring-danger focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-line disabled:hover:bg-surface disabled:hover:text-red-500/50"
                      title="Delete project"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </td>
              </motion.tr>
            ))}
          </Table>
        )}
      </section>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete project"
        message="This will permanently delete the project and all its records. This cannot be undone."
        confirmLabel="Delete"
        onConfirm={() => deleteTarget !== null && deleteMutation.mutate(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
        isPending={deleteMutation.isPending}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity log section
// ---------------------------------------------------------------------------

const EVENT_LABELS: Record<string, string> = {
  "analysis.started": "Analysis started",
  "analysis.ready": "Analysis ready",
  "analysis.failed": "Analysis failed",
  "extraction.started": "Extraction started",
  "extraction.completed": "Extraction completed",
  "extraction.failed": "Extraction failed",
  "project.canceled": "Canceled",
  "project.retried": "Retried"
};

function eventLabel(type: string): string {
  return EVENT_LABELS[type] ?? type;
}

function levelDot(level: string): string {
  if (level === "error") return "bg-danger";
  if (level === "warning") return "bg-warning";
  return "bg-teal";
}

type ActivityFilter = "all" | "errors" | "warnings" | "completed";

function matchesFilter(event: ProjectEvent, filter: ActivityFilter): boolean {
  if (filter === "all") return true;
  if (filter === "errors") return event.level === "error";
  if (filter === "warnings") return event.level === "warning";
  return event.event_type.endsWith("completed") || event.event_type === "analysis.ready";
}

function ActivityLogSection() {
  const [filter, setFilter] = useState<ActivityFilter>("all");

  const eventsQuery = useQuery({
    queryKey: ["dashboard-events"],
    queryFn: () => api.getDashboardEvents(100),
    refetchInterval: 10000,
    retry: false
  });

  const allEvents = Array.isArray(eventsQuery.data) ? eventsQuery.data : [];
  const filtered = allEvents.filter((event) => matchesFilter(event, filter));

  // Group by project, preserving the newest-first ordering from the API.
  const groups: { projectId: number; events: ProjectEvent[] }[] = [];
  const groupIndex = new Map<number, number>();
  for (const event of filtered) {
    let idx = groupIndex.get(event.project_id);
    if (idx === undefined) {
      idx = groups.length;
      groupIndex.set(event.project_id, idx);
      groups.push({ projectId: event.project_id, events: [] });
    }
    groups[idx].events.push(event);
  }

  const filters: { key: ActivityFilter; label: string }[] = [
    { key: "all", label: "All" },
    { key: "errors", label: "Errors" },
    { key: "warnings", label: "Warnings" },
    { key: "completed", label: "Completed" }
  ];

  return (
    <section className="rounded-xl border border-line bg-surface shadow-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-6 py-4">
        <h2 className="text-xs font-bold uppercase tracking-widest text-muted">Activity log</h2>
        <div className="flex items-center gap-1">
          {filters.map((option) => (
            <button
              key={option.key}
              onClick={() => setFilter(option.key)}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                filter === option.key
                  ? "bg-teal text-white"
                  : "text-muted hover:bg-porcelain hover:text-ink"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {eventsQuery.isLoading ? (
        <div className="grid gap-3 p-6">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : eventsQuery.error ? (
        <div className="p-6">
          <Alert tone="danger">Could not load activity.</Alert>
        </div>
      ) : groups.length === 0 ? (
        <p className="px-6 py-10 text-center text-sm text-muted">
          {filter === "all" ? "No activity yet." : "No matching activity."}
        </p>
      ) : (
        <div className="divide-y divide-line">
          {groups.map((group) => (
            <div key={group.projectId} className="px-6 py-4">
              <Link
                to={`/projects/${group.projectId}`}
                className="mb-2 inline-block text-sm font-bold text-ink transition hover:text-teal"
              >
                Project #{group.projectId}
              </Link>
              <ul className="grid gap-1.5">
                {group.events.map((event) => (
                  <li key={event.id} className="flex items-start gap-3 text-sm">
                    <span
                      className={`mt-1.5 h-2 w-2 flex-none rounded-full ${levelDot(event.level)}`}
                      aria-hidden="true"
                    />
                    <span className="w-28 flex-none whitespace-nowrap text-xs text-muted">
                      {formatDate(event.created_at)}
                    </span>
                    <span className="flex-none font-semibold text-ink">
                      {eventLabel(event.event_type)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-muted" title={event.message}>
                      {event.message}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
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
      toast.success("Task deleted");
    },
    onError: (err) => {
      setDeleteTaskTarget(null);
      toast.error(err instanceof Error ? err.message : "Failed to delete task");
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
      <PageHeader title="Dashboard" eyebrow="Extraction overview">
        <Link to="/projects/new">
          <Button>
            <BrainCog className="h-4 w-4" />
            New extraction
          </Button>
        </Link>
      </PageHeader>

      <ProjectsSection />

      <div className="mt-6">
        <ActivityLogSection />
      </div>

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
