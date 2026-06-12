import { useMutation, useQuery } from "@tanstack/react-query";
import {
  BrainCog,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  Globe2,
  RefreshCw,
  Sparkles,
  Table2
} from "lucide-react";
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

const EXTRACTION_CHOICES: {
  value: ExtractionMode | "";
  title: string;
  desc: string;
  icon: typeof Table2;
  secondary?: boolean;
}[] = [
  {
    value: "STRUCTURED",
    title: "Structured data",
    desc: "Products, listings, tables, directories — extracted as rows and columns.",
    icon: Table2
  },
  {
    value: "CONTENT",
    title: "Content / documents",
    desc: "GitHub READMEs, docs, articles, blog posts — extracted as clean text.",
    icon: FileText
  },
  {
    value: "",
    title: "Let ScrapeGPT decide",
    desc: "Detect the most likely mode from the page automatically.",
    icon: Sparkles,
    secondary: true
  }
];

function ExtractionModeCards({
  value,
  onChange
}: {
  value: ExtractionMode | "";
  onChange: (value: ExtractionMode | "") => void;
}) {
  return (
    <div className="grid gap-2">
      {EXTRACTION_CHOICES.map((choice) => {
        const Icon = choice.icon;
        const active = value === choice.value;
        return (
          <button
            key={choice.value || "auto"}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(choice.value)}
            className={`flex items-start gap-3 rounded-lg border p-3 text-left transition ${
              active
                ? "border-teal bg-teal-soft/60 ring-1 ring-teal/30"
                : "border-line bg-surface hover:border-teal/50 hover:bg-porcelain"
            }`}
          >
            <span
              className={`mt-0.5 grid h-8 w-8 flex-shrink-0 place-items-center rounded-md ${
                active ? "bg-teal text-white" : "bg-porcelain text-muted"
              }`}
            >
              <Icon className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="text-sm font-semibold text-ink">{choice.title}</span>
                {choice.secondary ? (
                  <span className="rounded border border-line px-1 py-0.5 text-[10px] font-bold uppercase tracking-wide text-muted">
                    auto
                  </span>
                ) : null}
              </span>
              <span className="mt-0.5 block text-xs text-muted">{choice.desc}</span>
            </span>
            {active ? <Check className="mt-0.5 h-4 w-4 flex-shrink-0 text-teal" /> : null}
          </button>
        );
      })}
    </div>
  );
}

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
        <section className="card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
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

            <button
              type="button"
              className="flex h-10 items-center justify-between rounded-md border border-line px-3 text-sm font-semibold text-muted transition hover:bg-porcelain hover:text-ink"
              onClick={() => setConnectionOpen((value) => !value)}
            >
              <span>Advanced options</span>
              {connectionOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>

            {connectionOpen ? (
              <div className="grid gap-4 rounded-lg border border-line bg-porcelain p-4">
                <div className="grid gap-2">
                  <span className="text-sm font-semibold text-ink">What are you extracting?</span>
                  <ExtractionModeCards value={extractionMode} onChange={setExtractionMode} />
                  <p className="text-xs text-muted">
                    Scraping GitHub, docs, or articles? Choose{" "}
                    <span className="font-semibold text-ink">Content / documents</span>.
                  </p>
                </div>

                <Field
                  label="How should ScrapeGPT load the page?"
                  hint="Use browser rendering if the preview comes back empty or the page is heavily JavaScript-driven (e.g. GitHub-style apps)."
                >
                  <Select
                    value={renderMode}
                    onChange={(event) => setRenderMode(event.target.value as RenderMode)}
                  >
                    <option value="AUTO">Automatic (recommended)</option>
                    <option value="STATIC">Static HTML — fastest, for simple pages</option>
                    <option value="BROWSER">Browser rendering — for JavaScript-heavy pages</option>
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

        <section className="card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
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
