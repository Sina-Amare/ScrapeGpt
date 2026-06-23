import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Plus, RefreshCw, Trash2 } from "lucide-react";
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
import { Table } from "../components/ui/Table";
import { ApiError, api } from "../lib/api";
import { projectTone, TERMINAL_PROJECT_STATES } from "../lib/projectPolling";

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function truncateUrl(url: string, max = 58): string {
  return url.length > max ? url.slice(0, max) + "..." : url;
}

export function ProjectsPage() {
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(100),
    refetchInterval: 5000
  });

  const deleteMutation = useMutation({
    mutationFn: api.deleteProject,
    onSuccess: () => {
      setDeleteTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Project deleted");
    },
    onError: (error) => {
      setDeleteTarget(null);
      if (error instanceof ApiError && error.status === 400) {
        toast.error("Active projects cannot be deleted. Cancel or wait for completion first.");
        return;
      }
      toast.error(error instanceof Error ? error.message : "Could not delete project");
    }
  });

  return (
    <>
      <PageHeader title="Projects" eyebrow="Extraction workspace">
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => void projects.refetch()}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
          <Link to="/projects/new">
            <Button>
              <Plus className="h-4 w-4" />
              New extraction
            </Button>
          </Link>
        </div>
      </PageHeader>

      {projects.isLoading ? (
        <div className="grid gap-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : projects.error ? (
        <Alert tone="danger">Could not load projects.</Alert>
      ) : !projects.data?.length ? (
        <div className="card-hover rounded-lg border border-line bg-surface p-12 text-center shadow-panel">
          <p className="text-sm text-muted">
            No projects yet.{" "}
            <Link to="/projects/new" className="font-semibold text-teal hover:text-teal-dark">
              Analyze your first URL →
            </Link>
          </p>
        </div>
      ) : (
        <Table headings={["#", "URL", "Type", "Status", "Fields", "Confidence", "Updated", "Actions"]}>
          {projects.data.map((project, index) => (
            <motion.tr
              key={project.id}
              className="transition-colors hover:bg-teal-soft/40"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(index, 8) * 0.045, duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            >
              <td className="px-4 py-3 font-mono text-sm font-semibold text-ink">
                {project.id}
              </td>
              <td className="max-w-xs px-4 py-3">
                <span className="block truncate text-sm text-muted" title={project.url}>
                  {truncateUrl(project.url)}
                </span>
                {project.error ? (
                  <span className="mt-0.5 block truncate text-xs text-danger" title={project.error}>
                    {project.error}
                  </span>
                ) : null}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                {project.detected_type ?? project.extraction_mode}
              </td>
              <td className="px-4 py-3">
                <Badge tone={projectTone(project)}>{project.product_status_label}</Badge>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                {project.selected_field_count}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                {project.confidence != null ? `${Math.round(project.confidence * 100)}%` : project.confidence_label}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">
                {formatDate(project.last_activity)}
              </td>
              <td className="whitespace-nowrap px-4 py-3">
                <div className="flex gap-2">
                  <Link to={`/projects/${project.id}`}>
                    <button
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-muted transition hover:border-teal hover:bg-teal-soft hover:text-teal focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-teal"
                      title="Open project"
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                  </Link>
                  <button
                    onClick={() => setDeleteTarget(project.id)}
                    disabled={!TERMINAL_PROJECT_STATES.has(project.system_state) || deleteMutation.isPending}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-danger/70 transition hover:border-danger hover:bg-danger/10 hover:text-danger focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-danger disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-line disabled:hover:bg-surface disabled:hover:text-danger/50"
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

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete project"
        message="This will permanently delete the project and all its records. This cannot be undone."
        confirmLabel="Delete"
        onConfirm={() => deleteTarget !== null && deleteMutation.mutate(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
        isPending={deleteMutation.isPending}
      />
    </>
  );
}
