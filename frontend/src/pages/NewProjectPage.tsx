import { useMutation, useQuery } from "@tanstack/react-query";
import { BrainCog, ChevronDown, ChevronRight, Globe2, RefreshCw } from "lucide-react";
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { AnalysisPipeline } from "../components/project/AnalysisPipeline";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Select } from "../components/ui/Select";
import { ApiError, api } from "../lib/api";
import { projectTone, shouldPollProject } from "../lib/projectPolling";
import { ExtractionMode, ProjectResponse, RenderMode } from "../types";

function admissionError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    const detail = error.detail as Record<string, unknown> | null;
    const nested = detail?.detail as Record<string, string> | undefined;
    if (nested?.error_code === "NO_PROVIDER_CONFIGURED") {
      return "Add an AI provider before starting an extraction project.";
    }
    if (nested?.error_code === "ACTIVE_JOB_LIMIT_REACHED") {
      return "You have too many active projects. Wait for one to finish first.";
    }
    return "Could not start this project because setup or limits need attention.";
  }
  return error instanceof Error ? error.message : "Could not start extraction project";
}

export function NewProjectPage() {
  const [url, setUrl] = useState("");
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [extractionMode, setExtractionMode] = useState<ExtractionMode | "">("");
  const [renderMode, setRenderMode] = useState<RenderMode>("AUTO");
  const [providerConfigId, setProviderConfigId] = useState("");
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [failureCount, setFailureCount] = useState(0);

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: api.listProviders
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.analyzeProject({
        url,
        advanced: {
          extraction_mode: extractionMode ? extractionMode : undefined,
          workflow_mode: "GUIDED",
          render_mode: renderMode !== "AUTO" ? renderMode : undefined,
          provider_config_id: providerConfigId ? Number(providerConfigId) : null
        }
      }),
    onSuccess: (response) => {
      setProject(response);
      setFailureCount(0);
    }
  });

  const projectQuery = useQuery({
    queryKey: ["project", project?.id],
    enabled: Boolean(project?.id),
    queryFn: async () => {
      if (!project) return null;
      try {
        const response = await api.getProject(project.id);
        setFailureCount(0);
        return response;
      } catch (err) {
        setFailureCount((count) => count + 1);
        throw err;
      }
    },
    refetchInterval: (query) =>
      shouldPollProject(query.state.data ?? project, failureCount) ? 2000 : false,
    retry: false
  });

  const current = projectQuery.data ?? project;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  return (
    <>
      <PageHeader title="New Extraction" eyebrow="Project setup" />

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
          <div className="mb-6 flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-md bg-teal-soft text-teal">
              <BrainCog className="h-5 w-5" />
            </div>
            <div>
              <h2 className="font-bold text-ink">Analyze a page</h2>
              <p className="text-sm text-muted">
                Paste a URL and ScrapeGPT will find extractable data.
              </p>
            </div>
          </div>

          {createMutation.error ? (
            <div className="mb-4">
              <Alert tone="danger">{admissionError(createMutation.error)}</Alert>
            </div>
          ) : null}

          <form className="grid gap-4" onSubmit={onSubmit}>
            <Field label="URL">
              <Input
                type="url"
                placeholder="https://example.com/products"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                required
              />
            </Field>

            <Field label="What are you extracting?" hint="Leave blank and ScrapeGPT will decide.">
              <Select
                value={extractionMode}
                onChange={(event) => setExtractionMode(event.target.value as ExtractionMode | "")}
              >
                <option value="">Let ScrapeGPT decide</option>
                <option value="STRUCTURED">Structured data - products, listings, directories, tables</option>
                <option value="CONTENT">Content - articles, docs, knowledge pages</option>
              </Select>
            </Field>

            <button
              type="button"
              className="flex h-10 items-center justify-between rounded-md border border-line px-3 text-sm font-semibold text-muted transition hover:bg-porcelain hover:text-ink"
              onClick={() => setConnectionOpen((value) => !value)}
            >
              <span>Connection and rendering</span>
              {connectionOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>

            {connectionOpen ? (
              <div className="grid gap-4 rounded-lg border border-line bg-porcelain p-4">
                <Field
                  label="Page rendering"
                  hint="Use browser rendering if the page is empty or heavily interactive."
                >
                  <Select
                    value={renderMode}
                    onChange={(event) => setRenderMode(event.target.value as RenderMode)}
                  >
                    <option value="AUTO">Automatic</option>
                    <option value="STATIC">Static HTML only</option>
                    <option value="BROWSER">Browser rendering</option>
                  </Select>
                </Field>

                <Field label="AI provider" hint="Leave blank to use your default provider.">
                  <Select
                    value={providerConfigId}
                    onChange={(event) => setProviderConfigId(event.target.value)}
                  >
                    <option value="">Default provider</option>
                    {providers.data?.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.name} ({provider.provider} / {provider.model})
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
            ) : null}

            <Button type="submit" disabled={createMutation.isPending || Boolean(project)}>
              <Globe2 className="h-4 w-4" />
              {createMutation.isPending ? "Starting..." : project ? "Project started" : "Analyze URL"}
            </Button>
          </form>
        </section>

        <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <h2 className="font-bold text-ink">Project status</h2>
              <p className="text-sm text-muted">
                {current ? "Follow the analysis, then choose fields." : "Submit a URL to begin."}
              </p>
            </div>
            {current ? (
              <Button variant="secondary" onClick={() => void projectQuery.refetch()}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
            ) : null}
          </div>

          {!current ? (
            <div className="rounded-lg border border-dashed border-line bg-porcelain p-10 text-center text-sm text-muted">
              A project will appear here after analysis starts.
            </div>
          ) : failureCount >= 3 ? (
            <Alert tone="danger">Polling paused after repeated failures. Use Refresh to resume.</Alert>
          ) : (
            <div className="grid gap-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h3 className="text-lg font-bold text-ink">Project #{current.id}</h3>
                  <p className="break-all text-sm text-muted">{current.url}</p>
                </div>
                <Badge tone={projectTone(current)}>{current.product_status_label}</Badge>
              </div>

              {current.error ? <Alert tone="danger">{current.error}</Alert> : null}

              {current.product_status === "ready_to_review" ||
              current.product_status === "preview_ready" ||
              current.product_status === "completed" ? (
                <Link className="text-sm font-bold text-teal hover:text-teal-dark" to={`/projects/${current.id}`}>
                  Continue to field selection -&gt;
                </Link>
              ) : current.system_state === "FAILED" || current.system_state === "CANCELED" ? (
                <Alert tone="danger">{current.error ?? "Analysis failed."}</Alert>
              ) : (
                <AnalysisPipeline state={current.system_state} />
              )}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
