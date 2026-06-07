import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Download, RefreshCw, Save, XCircle } from "lucide-react";
import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Select } from "../components/ui/Select";
import { Skeleton } from "../components/ui/Skeleton";
import { ApiError, api } from "../lib/api";
import { ACTIVE_PROJECT_STATES, projectTone, shouldPollProject } from "../lib/projectPolling";
import { FieldSpec, ProjectRecord } from "../types";

function ConfidenceBar({ value }: { value: number | null }) {
  const pct = value == null ? 0 : Math.round(value * 100);
  const color = pct >= 80 ? "bg-success" : pct >= 60 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 text-right text-sm font-bold text-ink">{value == null ? "-" : `${pct}%`}</span>
    </div>
  );
}

function asString(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function RecordsTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = useMemo(
    () => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 12),
    [rows]
  );
  if (!rows.length) {
    return <p className="rounded-lg border border-line bg-porcelain p-6 text-center text-sm text-muted">No rows yet.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
            {columns.map((column) => (
              <th key={column} className="px-4 py-2.5">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-surface">
          {rows.map((row, index) => (
            <tr key={index} className="hover:bg-teal-soft/30">
              {columns.map((column) => (
                <td key={column} className="max-w-xs truncate px-4 py-3 text-muted" title={asString(row[column])}>
                  {asString(row[column]) || "-"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FieldEditor({
  fields,
  onChange
}: {
  fields: FieldSpec[];
  onChange: (fields: FieldSpec[]) => void;
}) {
  function updateField(index: number, patch: Partial<FieldSpec>) {
    onChange(fields.map((field, i) => (i === index ? { ...field, ...patch } : field)));
  }

  if (!fields.length) {
    return <Alert tone="info">No fields are available yet. Wait for analysis to finish.</Alert>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
            <th className="px-4 py-2.5">Use</th>
            <th className="px-4 py-2.5">Field name</th>
            <th className="px-4 py-2.5">Type</th>
            <th className="px-4 py-2.5">Required</th>
            <th className="px-4 py-2.5">Confidence</th>
            <th className="px-4 py-2.5">Samples</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-surface">
          {fields.map((field, index) => (
            <tr key={`${field.name}-${index}`} className="hover:bg-teal-soft/30">
              <td className="px-4 py-3">
                <input
                  className="h-4 w-4 accent-teal"
                  type="checkbox"
                  checked={field.selected}
                  onChange={(event: ChangeEvent<HTMLInputElement>) =>
                    updateField(index, { selected: event.target.checked })
                  }
                />
              </td>
              <td className="min-w-56 px-4 py-3">
                <Input
                  value={field.user_label ?? field.label ?? field.name ?? ""}
                  onChange={(event) => updateField(index, { user_label: event.target.value })}
                />
              </td>
              <td className="min-w-36 px-4 py-3">
                <Select value={field.type} onChange={(event) => updateField(index, { type: event.target.value })}>
                  <option value="string">Text</option>
                  <option value="number">Number</option>
                  <option value="url">URL</option>
                  <option value="date">Date</option>
                  <option value="boolean">Boolean</option>
                  <option value="image">Image</option>
                </Select>
              </td>
              <td className="px-4 py-3">
                <input
                  className="h-4 w-4 accent-teal"
                  type="checkbox"
                  checked={field.required}
                  onChange={(event: ChangeEvent<HTMLInputElement>) =>
                    updateField(index, { required: event.target.checked })
                  }
                />
              </td>
              <td className="whitespace-nowrap px-4 py-3 font-bold text-muted">
                {field.confidence == null ? "-" : `${Math.round(field.confidence * 100)}%`}
              </td>
              <td className="max-w-xs truncate px-4 py-3 text-muted">
                {(field.sample_values ?? []).slice(0, 2).join(", ") || "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projectId = Number(id);
  const [failureCount, setFailureCount] = useState(0);
  const [fields, setFields] = useState<FieldSpec[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);

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
    retry: false
  });

  const recordsQuery = useQuery({
    queryKey: ["project-records", projectId],
    queryFn: () => api.listProjectRecords(projectId),
    enabled: projectQuery.data?.system_state === "COMPLETED",
    retry: false
  });

  const project = projectQuery.data;

  useEffect(() => {
    if (project?.spec?.fields) {
      setFields(project.spec.fields);
    }
  }, [project?.spec?.fields, project?.spec?.id]);

  const saveSpec = useMutation({
    mutationFn: () =>
      api.updateProjectSpec(projectId, {
        fields,
        page_limit: project?.spec?.page_limit,
        export_format: project?.spec?.export_format
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });

  const previewMutation = useMutation({
    mutationFn: () => api.previewProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });

  const extractMutation = useMutation({
    mutationFn: () => api.extractProject(projectId, false),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project-records", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });

  const records: ProjectRecord[] = recordsQuery.data ?? [];

  return (
    <>
      <PageHeader title={project ? `Project #${project.id}` : "Project"} eyebrow="Extraction workspace">
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
          {project && ACTIVE_PROJECT_STATES.has(project.system_state) ? (
            <Button variant="danger" onClick={() => cancelMutation.mutate()}>
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
        <div className="grid gap-6">
          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <h2 className="break-all text-xl font-bold text-ink">{project.url}</h2>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Badge tone={projectTone(project)}>{project.product_status_label}</Badge>
                  <Badge tone="neutral">{project.detected_type ?? project.extraction_mode}</Badge>
                  <Badge tone="neutral">{project.selected_field_count} selected fields</Badge>
                </div>
              </div>
              <div className="w-full rounded-lg border border-line bg-porcelain p-4 md:w-72">
                <p className="mb-2 text-xs font-bold uppercase tracking-widest text-muted">Confidence</p>
                <ConfidenceBar value={project.confidence} />
              </div>
            </div>
            {project.error ? <div className="mt-5"><Alert tone="danger">{project.error}</Alert></div> : null}
            {project.warnings.length ? (
              <div className="mt-5">
                <Alert tone="info">
                  <ul className="list-disc space-y-1 pl-4">
                    {project.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
                  </ul>
                </Alert>
              </div>
            ) : null}
          </section>

          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-bold text-ink">Fields</h2>
                <p className="text-sm text-muted">Choose the data to extract and adjust field labels.</p>
              </div>
              <Button onClick={() => saveSpec.mutate()} disabled={!fields.length || saveSpec.isPending}>
                <Save className="h-4 w-4" />
                {saveSpec.isPending ? "Saving..." : "Save fields"}
              </Button>
            </div>
            {saveSpec.error ? <Alert tone="danger">{saveSpec.error.message}</Alert> : null}
            <FieldEditor fields={fields} onChange={setFields} />
          </section>

          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-bold text-ink">Preview</h2>
                <p className="text-sm text-muted">Check a sample before running extraction.</p>
              </div>
              <Button onClick={() => previewMutation.mutate()} disabled={!project.spec || previewMutation.isPending}>
                <Check className="h-4 w-4" />
                {previewMutation.isPending ? "Preparing..." : "Preview data"}
              </Button>
            </div>
            {previewMutation.error ? <Alert tone="danger">{previewMutation.error.message}</Alert> : null}
            {project.preview ? (
              <div className="grid gap-4">
                <RecordsTable rows={project.preview.sample_records} />
                {project.preview.missing_fields.length ? (
                  <Alert tone="info">{project.preview.missing_fields.length} selected fields had no sample value.</Alert>
                ) : null}
              </div>
            ) : (
              <p className="rounded-lg border border-dashed border-line bg-porcelain p-6 text-center text-sm text-muted">
                Save fields, then preview sample rows.
              </p>
            )}
          </section>

          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-bold text-ink">Extraction</h2>
                <p className="text-sm text-muted">Run extraction from the saved field selection.</p>
              </div>
              <Button onClick={() => extractMutation.mutate()} disabled={!project.preview || extractMutation.isPending}>
                <Download className="h-4 w-4" />
                {extractMutation.isPending ? "Extracting..." : "Extract"}
              </Button>
            </div>
            {extractMutation.error ? <Alert tone="danger">{extractMutation.error.message}</Alert> : null}
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="rounded-lg border border-line bg-porcelain p-4">
                <p className="text-xs font-bold uppercase tracking-widest text-muted">Pages</p>
                <p className="mt-1 text-xl font-bold text-ink">{project.progress.crawl_pages_total}</p>
              </div>
              <div className="rounded-lg border border-line bg-porcelain p-4">
                <p className="text-xs font-bold uppercase tracking-widest text-muted">Records</p>
                <p className="mt-1 text-xl font-bold text-ink">{project.progress.extracted_records_total}</p>
              </div>
              <div className="rounded-lg border border-line bg-porcelain p-4">
                <p className="text-xs font-bold uppercase tracking-widest text-muted">Exports</p>
                <p className="mt-1 text-xl font-bold text-ink">{project.progress.exports_total}</p>
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-bold text-ink">Results</h2>
                <p className="text-sm text-muted">Extracted records from this project.</p>
              </div>
              {records.length ? (
                <a href={`/api/v1/projects/${project.id}/export?format=csv`}>
                  <Button variant="secondary">
                    <Download className="h-4 w-4" />
                    CSV
                  </Button>
                </a>
              ) : null}
            </div>
            <RecordsTable rows={records.map((record) => record.normalized_data ?? record.raw_data)} />
          </section>

          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <button
              type="button"
              className="text-sm font-bold text-muted transition hover:text-ink"
              onClick={() => setShowAdvanced((value) => !value)}
            >
              {showAdvanced ? "Hide advanced" : "Show advanced"}
            </button>
            {showAdvanced ? (
              <pre className="mt-4 overflow-x-auto rounded-lg border border-line bg-porcelain p-4 text-xs text-ink">
                {JSON.stringify(
                  {
                    system_state: project.system_state,
                    render_mode: project.render_mode,
                    workflow_mode: project.workflow_mode,
                    fetch_metadata: project.fetch_metadata,
                    analysis: project.analysis,
                    spec: project.spec
                  },
                  null,
                  2
                )}
              </pre>
            ) : null}
          </section>
        </div>
      ) : null}
    </>
  );
}
