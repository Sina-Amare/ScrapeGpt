import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { WorkspaceWizard } from "../components/workspace/WorkspaceWizard";
import { Alert } from "../components/ui/Alert";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { ApiError, api } from "../lib/api";
import { ACTIVE_PROJECT_STATES, shouldPollProject } from "../lib/projectPolling";

/**
 * Thin orchestrator for the extraction workspace: owns the project query +
 * polling, 404/loading/error handling, the page header actions (back / refresh /
 * cancel), and the cancel dialog. The entire guided flow lives in
 * <WorkspaceWizard/>.
 */
export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projectId = Number(id);
  const [failureCount, setFailureCount] = useState(0);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    enabled: !Number.isNaN(projectId),
    queryFn: async () => {
      try {
        const response = await api.getProject(projectId);
        setFailureCount(0);
        return response;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          navigate("/projects", { replace: true });
        }
        setFailureCount((count) => count + 1);
        throw error;
      }
    },
    refetchInterval: (query) =>
      shouldPollProject(query.state.data, failureCount) ? 2000 : false,
    retry: false,
  });

  const project = projectQuery.data;
  const isActive = project ? ACTIVE_PROJECT_STATES.has(project.system_state) : false;

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  return (
    <>
      <PageHeader
        title={project ? `Project #${project.id}` : "Project"}
        eyebrow="Extraction workspace"
      >
        <div className="flex flex-wrap gap-2">
          <Link to="/projects">
            <Button variant="secondary">
              <ArrowLeft className="h-4 w-4" />
              All projects
            </Button>
          </Link>
          <Button variant="secondary" onClick={() => void projectQuery.refetch()}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
          {isActive ? (
            <Button
              variant="danger"
              onClick={() => setShowCancelConfirm(true)}
              disabled={cancelMutation.isPending}
              loading={cancelMutation.isPending}
            >
              <XCircle className="h-4 w-4" />
              Cancel
            </Button>
          ) : null}
        </div>
      </PageHeader>

      {projectQuery.isLoading ? (
        <div className="grid gap-4">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : projectQuery.error ? (
        <Alert tone="danger">Could not load project.</Alert>
      ) : project ? (
        <WorkspaceWizard project={project} />
      ) : null}

      <ConfirmDialog
        open={showCancelConfirm}
        title="Cancel extraction"
        message="This will stop the current extraction run. Partial results will be kept. Continue?"
        confirmLabel="Yes, cancel"
        variant="primary"
        onConfirm={() => {
          setShowCancelConfirm(false);
          cancelMutation.mutate();
        }}
        onCancel={() => setShowCancelConfirm(false)}
        isPending={cancelMutation.isPending}
      />
    </>
  );
}
