import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Check, Download, Info, RefreshCw, Save, XCircle } from "lucide-react";
import { motion } from "motion/react";
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { AnalysisPipeline } from "../components/project/AnalysisPipeline";
import { FrontierPreviewPanel } from "../components/project/FrontierPreviewPanel";
import { PaginatedResultsTable } from "../components/project/PaginatedResultsTable";
import { ScopeSelector } from "../components/project/ScopeSelector";
import { TrustSummaryPanel } from "../components/project/TrustSummaryPanel";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Select } from "../components/ui/Select";
import { Skeleton } from "../components/ui/Skeleton";
import { ApiError, api } from "../lib/api";
import { ACTIVE_PROJECT_STATES, projectTone, shouldPollProject } from "../lib/projectPolling";
import { isUserConfirmed, requiresConfirmation, scopeModeLabel } from "../lib/scopeCopy";
import { BrowserSession, CrawlScope, CrawlScopeMode, CrawlScopeStatus, FieldSpec, ProjectRecord } from "../types";

function ConfidenceBar({ value }: { value: number | null }) {
  const pct = value == null ? 0 : Math.round(value * 100);
  const color = pct >= 80 ? "bg-success" : pct >= 60 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-2 flex-1 min-w-0 overflow-hidden rounded-full bg-gray-100">
        <motion.div
          className={`absolute inset-y-0 left-0 rounded-full ${color}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
        />
      </div>
      <span className="w-12 shrink-0 text-right text-sm font-bold text-ink">
        {value == null ? "-" : `${pct}%`}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sticky tab navigation with scroll-spy
// ---------------------------------------------------------------------------

const TABS = [
  { label: "Overview", id: "overview" },
  { label: "Scope",    id: "scope" },
  { label: "Fields",   id: "fields" },
  { label: "Preview",  id: "preview" },
  { label: "Extract",  id: "extract" },
  { label: "Quality",  id: "quality" },
  { label: "Results",  id: "results" },
];

function ProjectTabs({ activeTab }: { activeTab: string }) {
  function scrollTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  return (
    <div className="sticky top-16 z-20 -mx-4 mb-6 border-b border-line bg-surface/95 backdrop-blur px-4 md:-mx-8 md:px-8">
      <div className="flex gap-1 overflow-x-auto scrollbar-none">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => scrollTo(tab.id)}
            className={`relative shrink-0 px-4 py-3 text-sm font-semibold transition ${
              activeTab === tab.id ? "text-teal" : "text-muted hover:text-ink"
            }`}
          >
            {tab.label}
            {activeTab === tab.id && (
              <motion.div
                layoutId="tab-underline"
                className="absolute bottom-0 left-0 right-0 h-0.5 bg-teal"
                transition={{ type: "spring", stiffness: 500, damping: 40 }}
              />
            )}
          </button>
        ))}
      </div>
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
    <div className="space-y-3">
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-porcelain text-left text-xs font-bold uppercase tracking-widest text-muted">
            <th className="px-4 py-2.5">
              <span className="flex items-center gap-1">
                Use
                <span title="Include this field in your output. Uncheck to skip it entirely." className="cursor-help">
                  <Info className="h-3.5 w-3.5 text-muted/60" />
                </span>
              </span>
            </th>
            <th className="px-4 py-2.5">Field name</th>
            <th className="px-4 py-2.5">
              <span className="flex items-center gap-1">
                Type
                <span title="Auto-detected by AI — change only if the type is wrong." className="cursor-help">
                  <Info className="h-3.5 w-3.5 text-muted/60" />
                </span>
              </span>
            </th>
            <th className="px-4 py-2.5">
              <span className="flex items-center gap-1">
                Required
                <span title="Discard any row where this field is empty. Only check for fields that must appear on every row." className="cursor-help">
                  <Info className="h-3.5 w-3.5 text-muted/60" />
                </span>
              </span>
            </th>
            <th className="px-4 py-2.5">Confidence</th>
            <th className="px-4 py-2.5">Samples</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-surface">
          {fields.map((field, index) => (
            <tr key={`${field.name ?? ""}-${index}`} className="hover:bg-teal-soft/30">
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
                <Select
                  value={field.type}
                  title="Auto-detected by AI — change only if incorrect"
                  onChange={(event) => updateField(index, { type: event.target.value })}
                >
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
    <p className="text-xs text-muted">
      <strong>Tip:</strong> Check <strong>Use</strong> for every field you want in your output. Only mark <strong>Required</strong> for fields like title or ID that must appear on every row — rows missing a required field are dropped from the results.
    </p>
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
  const [pageLimit, setPageLimit] = useState(500);
  const [exportFormat, setExportFormat] = useState("csv");
  const [showDeveloper, setShowDeveloper] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");
  const observerRef = useRef<IntersectionObserver | null>(null);

  // Crawl scope draft state
  const [draftMode, setDraftMode] = useState<CrawlScopeMode | null>(null);
  // Stale preview tracking: true when scope was saved after the last preview was generated
  const [scopeChangedAfterPreview, setScopeChangedAfterPreview] = useState(false);
  // Scope confirmation error from extract 409
  const [extractScopeError, setExtractScopeError] = useState<string | null>(null);
  // Preview-gate error (NO_PREVIEW / STALE_PREVIEW / ZERO_PREVIEW_RECORDS) with bypass option
  const [extractGateError, setExtractGateError] = useState<{
    code: string;
    message: string;
  } | null>(null);

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

  const project = projectQuery.data;
  const savedScope = project?.spec?.crawl_scope ?? null;

  // Compute the effective scope for display: merge saved scope with local draft mode.
  const effectiveDraftMode: CrawlScopeMode =
    draftMode ?? savedScope?.mode ?? "CURRENT_PAGE";
  const effectiveScope: CrawlScope | null = savedScope
    ? {
        ...savedScope,
        mode: effectiveDraftMode,
        // clear confirmation when mode diverges from what's saved
        status: effectiveDraftMode !== savedScope.mode
          ? ("AI_SUGGESTED" as CrawlScopeStatus)
          : savedScope.status,
        user_confirmed_at:
          effectiveDraftMode !== savedScope.mode ? null : savedScope.user_confirmed_at,
      }
    : null;

  const scopeNeedsConfirmation =
    requiresConfirmation(effectiveDraftMode) &&
    !isUserConfirmed(effectiveScope?.status);

  useEffect(() => {
    if (project?.spec?.fields) {
      setFields(project.spec.fields);
      setPageLimit(project.spec.page_limit);
      setExportFormat(project.spec.export_format);
    }
    // Sync draft mode when spec changes (e.g. after save)
    if (project?.spec?.crawl_scope?.mode && draftMode === null) {
      setDraftMode(project.spec.crawl_scope.mode);
    }
  }, [project?.spec?.fields, project?.spec?.id, project?.spec?.page_limit, project?.spec?.export_format, project?.spec?.crawl_scope?.mode, draftMode]);

  useEffect(() => {
    observerRef.current?.disconnect();
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) setActiveTab(visible[0].target.id);
      },
      { rootMargin: "-20% 0px -70% 0px", threshold: 0 }
    );
    observerRef.current = observer;
    TABS.forEach(({ id }) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [project]);

  const saveSpec = useMutation({
    mutationFn: () =>
      api.updateProjectSpec(projectId, {
        fields,
        page_limit: pageLimit,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });

  // Save crawl scope mode (without confirmation)
  const saveScopeMutation = useMutation({
    mutationFn: (newMode: CrawlScopeMode) =>
      api.updateProjectSpec(projectId, {
        crawl_scope: {
          ...(savedScope ?? {}),
          mode: newMode,
        } as Partial<CrawlScope>
      }),
    onSuccess: () => {
      setScopeChangedAfterPreview(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    }
  });

  // Confirm crawl scope
  const confirmScopeMutation = useMutation({
    mutationFn: () =>
      api.updateProjectSpec(projectId, {
        crawl_scope: {
          ...(savedScope ?? {}),
          mode: effectiveDraftMode,
          status: "USER_CONFIRMED" as CrawlScopeStatus,
          user_confirmed_at: new Date().toISOString(),
        } as Partial<CrawlScope>
      }),
    onSuccess: () => {
      setExtractScopeError(null);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    }
  });

  // Frontier preview
  const frontierPreviewMutation = useMutation({
    mutationFn: () => api.createFrontierPreview(projectId),
    onSuccess: () => {
      setScopeChangedAfterPreview(false);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    }
  });

  const retryMutation = useMutation({
    mutationFn: () => api.retryProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const setSessionMutation = useMutation({
    mutationFn: (sessionId: number | null) =>
      api.setProjectSession(projectId, sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const { data: sessions } = useQuery<BrowserSession[]>({
    queryKey: ["sessions"],
    queryFn: () => api.listSessions(),
  });

  const previewMutation = useMutation({
    mutationFn: () => api.previewProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });

  const extractMutation = useMutation({
    mutationFn: (extractAnyway: boolean) =>
      api.extractProject(projectId, extractAnyway),
    onSuccess: () => {
      setExtractScopeError(null);
      setExtractGateError(null);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project-records-page", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        const raw = error.detail as
          | { detail?: { error_code?: string; message?: string } | null }
          | null;
        const code = raw?.detail?.error_code;
        const message = raw?.detail?.message;
        if (code === "SCOPE_NOT_CONFIRMED") {
          setExtractScopeError(
            "Confirm what ScrapeGPT should crawl before extraction."
          );
          return;
        }
        if (
          code === "STALE_PREVIEW" ||
          code === "ZERO_PREVIEW_RECORDS" ||
          code === "NO_PREVIEW"
        ) {
          setExtractGateError({
            code,
            message: message ?? "Run preview before extracting.",
          });
          return;
        }
      }
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });

  const isCompleted = project?.system_state === "COMPLETED";
  const isActive = project ? ACTIVE_PROJECT_STATES.has(project.system_state) : false;

  // Legacy records fallback (used in sample preview only)
  const legacyRecordsQuery = useQuery({
    queryKey: ["project-records", projectId],
    queryFn: () => api.listProjectRecords(projectId),
    enabled: isCompleted,
    retry: false
  });
  const legacyRecords: ProjectRecord[] = legacyRecordsQuery.data ?? [];

  function handleModeChange(mode: CrawlScopeMode) {
    setDraftMode(mode);
    // Save immediately for CURRENT_PAGE; for broad modes, wait for confirmation click
    if (mode === "CURRENT_PAGE") {
      saveScopeMutation.mutate(mode);
    }
  }

  function handleConfirmScope() {
    confirmScopeMutation.mutate();
  }

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
        <div className="grid gap-6">
          <ProjectTabs activeTab={activeTab} />

          {/* Overview */}
          <section id="overview" className="scroll-mt-32 card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
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
            {project.error ? (
              <div className="mt-5 flex flex-col gap-2">
                <Alert tone="danger">
                  <div className="flex items-start justify-between gap-4">
                    <span>{project.error}</span>
                    {project.system_state === "FAILED" && (
                      <Button
                        variant="secondary"
                        onClick={() => retryMutation.mutate()}
                        disabled={retryMutation.isPending}
                      >
                        {retryMutation.isPending ? "Retrying…" : "Retry"}
                      </Button>
                    )}
                  </div>
                </Alert>
                {retryMutation.error && (
                  <Alert tone="danger">{retryMutation.error.message}</Alert>
                )}
                {project.error_code === "BOT_PROTECTION_BLOCKED" && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm">
                    <p className="font-medium text-amber-800">
                      ScrapeGPT cannot pass this bot protection automatically.
                    </p>
                    {(() => {
                      const domain = (() => {
                        try { return new URL(project.url).hostname; } catch { return ""; }
                      })();
                      const matching = (sessions ?? []).filter(
                        (s) => s.is_active && (s.domain === domain || domain.endsWith(`.${s.domain}`))
                      );
                      if (matching.length === 0) {
                        return (
                          <p className="mt-1 text-amber-700">
                            <Link to="/sessions" className="underline">
                              Add a browser session for {domain || "this domain"}
                            </Link>{" "}
                            in Settings → Sessions, then retry.
                          </p>
                        );
                      }
                      return (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <span className="text-amber-700">Use a saved session:</span>
                          <Select
                            value={String(project.browser_session_id ?? "")}
                            onChange={(e) => {
                              const val = e.target.value;
                              setSessionMutation.mutate(val ? Number(val) : null);
                            }}
                            className="text-sm"
                          >
                            <option value="">— Select session —</option>
                            {matching.map((s) => (
                              <option key={s.id} value={String(s.id)}>
                                {s.name}
                              </option>
                            ))}
                          </Select>
                          {setSessionMutation.error && (
                            <span className="text-xs text-red-600">
                              {setSessionMutation.error.message}
                            </span>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                )}
              </div>
            ) : null}
            {project.progress.crawl_pages_blocked > 0 && (
              <details className="mt-3">
                <summary className="cursor-pointer text-sm font-medium text-amber-700">
                  {project.progress.crawl_pages_blocked} page(s) blocked during extraction
                </summary>
                <ul className="mt-2 space-y-1 rounded-md border border-amber-100 bg-amber-50 p-3">
                  {(project.progress.blocked_pages_detail ?? []).map((p, i) => (
                    <li key={i} className="flex flex-wrap gap-2 text-xs text-gray-600">
                      <span className="font-mono max-w-sm truncate">{p.url}</span>
                      <span className="text-amber-700">{p.error ?? p.block_reason}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
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

          {/* Analysis Pipeline (shown while AI is working) */}
          {(project.system_state === "QUEUED" || project.system_state === "ANALYZING") && (
            <AnalysisPipeline state={project.system_state} />
          )}

          {/* Crawl Scope */}
          <section id="scope" className="scroll-mt-32 card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4">
              <h2 className="font-bold text-ink">Crawl scope</h2>
              <p className="text-sm text-muted">Choose what ScrapeGPT should crawl before extraction.</p>
            </div>
            {saveScopeMutation.error ? (
              <div className="mb-4"><Alert tone="danger">{saveScopeMutation.error.message}</Alert></div>
            ) : null}
            {confirmScopeMutation.error ? (
              <div className="mb-4"><Alert tone="danger">{confirmScopeMutation.error.message}</Alert></div>
            ) : null}
            {project.spec ? (
              <ScopeSelector
                crawlScope={effectiveScope}
                disabled={isActive || saveScopeMutation.isPending || confirmScopeMutation.isPending}
                onModeChange={handleModeChange}
                onConfirm={handleConfirmScope}
              />
            ) : (
              <p className="text-sm text-muted">Scope will be available after analysis.</p>
            )}
            {/* Show "save scope" button for broad modes that changed without confirming */}
            {draftMode !== null && draftMode !== savedScope?.mode && draftMode !== "CURRENT_PAGE" ? (
              <div className="mt-3 flex items-center gap-3">
                <AlertCircle className="h-4 w-4 text-warning" />
                <span className="text-sm text-muted">
                  Scope mode changed to <strong>{scopeModeLabel(draftMode)}</strong>. Save to confirm.
                </span>
                <Button
                  variant="secondary"
                  disabled={saveScopeMutation.isPending}
                  onClick={() => saveScopeMutation.mutate(draftMode)}
                >
                  {saveScopeMutation.isPending ? "Saving..." : "Save scope"}
                </Button>
              </div>
            ) : null}
          </section>

          {/* Frontier Preview */}
          <section id="preview" className="scroll-mt-32 card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4">
              <h2 className="font-bold text-ink">Page preview</h2>
              <p className="text-sm text-muted">Verify which URLs ScrapeGPT will actually visit before committing to a full extraction. Catches scope misconfiguration early.</p>
              <p className="mt-1 text-xs text-teal">Generate this before extracting — it shows the exact URL list so you can spot if the wrong pages are included.</p>
            </div>
            <FrontierPreviewPanel
              preview={project.frontier_preview}
              loading={frontierPreviewMutation.isPending}
              error={frontierPreviewMutation.error?.message ?? null}
              stale={scopeChangedAfterPreview}
              disabled={!project.spec || isActive}
              onGenerate={() => frontierPreviewMutation.mutate()}
            />
          </section>

          {/* Fields */}
          <section id="fields" className="scroll-mt-32 card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
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

          {/* Sample Preview */}
          <section id="sample" className="scroll-mt-32 card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-bold text-ink">Sample preview <span className="text-xs font-normal text-muted">(optional)</span></h2>
                <p className="text-sm text-muted">Runs the extraction on one page so you can verify field values before the full run.</p>
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

          {/* Extraction */}
          <section id="extract" className="scroll-mt-32 card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-bold text-ink">Extraction</h2>
                <p className="text-sm text-muted">Run extraction from the saved field selection.</p>
              </div>
              <Button
                onClick={() => { setExtractGateError(null); extractMutation.mutate(false); }}
                disabled={
                  !project.preview ||
                  extractMutation.isPending ||
                  scopeNeedsConfirmation
                }
                title={scopeNeedsConfirmation ? "Confirm the crawl scope before extracting" : undefined}
              >
                <Download className="h-4 w-4" />
                {extractMutation.isPending ? "Extracting..." : "Extract"}
              </Button>
            </div>

            {extractScopeError ? (
              <div className="mb-4">
                <Alert tone="danger">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                    <div>
                      <p className="font-semibold">{extractScopeError}</p>
                      <p className="mt-1 text-sm">
                        Confirm the crawl scope in the "Crawl scope" section above. Current mode:{" "}
                        <strong>{scopeModeLabel(effectiveDraftMode)}</strong>.
                      </p>
                    </div>
                  </div>
                </Alert>
              </div>
            ) : null}

            {extractGateError ? (
              <div className="mb-4">
                <Alert tone="info">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                    <div>
                      <p>{extractGateError.message}</p>
                      <button
                        className="mt-2 text-sm underline hover:no-underline"
                        onClick={() => extractMutation.mutate(true)}
                        disabled={extractMutation.isPending}
                      >
                        {extractMutation.isPending ? "Extracting…" : "Extract anyway"}
                      </button>
                    </div>
                  </div>
                </Alert>
              </div>
            ) : null}
            {extractMutation.error && !(extractMutation.error instanceof ApiError && extractMutation.error.status === 409) ? (
              <div className="mb-4"><Alert tone="danger">{extractMutation.error.message}</Alert></div>
            ) : null}

            {scopeNeedsConfirmation ? (
              <div className="mb-4">
                <Alert tone="info">
                  Confirm the crawl scope (<strong>{scopeModeLabel(effectiveDraftMode)}</strong>) before extraction can begin.
                </Alert>
              </div>
            ) : null}

            <div className="mb-5 grid gap-4 md:grid-cols-[220px_1fr]">
              <label className="grid gap-1 text-sm font-semibold text-ink">
                Safety limit
                <Input
                  type="number"
                  min={1}
                  max={5000}
                  value={pageLimit}
                  onChange={(event) => setPageLimit(Number(event.target.value))}
                />
                <span className="font-normal text-xs text-muted">Maximum pages to crawl</span>
              </label>
              <div className="rounded-lg border border-line bg-porcelain p-4 text-sm text-muted">
                ScrapeGPT will crawl pages within the selected scope, up to the safety limit.
                Scope is set in the "Crawl scope" section above.
              </div>
            </div>

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
            <div className="mt-4 grid gap-3 text-sm sm:grid-cols-5">
              <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
                Pending <strong className="text-ink">{project.progress.crawl_pages_pending}</strong>
              </span>
              <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
                Fetching <strong className="text-ink">{project.progress.crawl_pages_fetching}</strong>
              </span>
              <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
                Extracted <strong className="text-ink">{project.progress.crawl_pages_extracted}</strong>
              </span>
              <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
                Blocked <strong className="text-ink">{project.progress.crawl_pages_blocked}</strong>
              </span>
              <span className="rounded-md border border-line bg-surface px-3 py-2 text-muted">
                Failed <strong className="text-ink">{project.progress.crawl_pages_failed}</strong>
              </span>
            </div>
          </section>

          {/* Extraction Quality */}
          <section id="quality" className="scroll-mt-32 card-hover rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4">
              <h2 className="font-bold text-ink">Extraction quality</h2>
              <p className="text-sm text-muted">Trust signals for the extracted data.</p>
            </div>
            <TrustSummaryPanel quality={project.extraction_quality} />
          </section>

          {/* Results */}
          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-bold text-ink">Results</h2>
                <p className="text-sm text-muted">Extracted records from this project.</p>
              </div>
              {legacyRecords.length ? (
                <div className="flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-2 text-sm text-muted">
                    Export:
                    <Select
                      value={exportFormat}
                      onChange={(e) => setExportFormat(e.target.value)}
                    >
                      <option value="csv">CSV</option>
                      <option value="json">JSON</option>
                      <option value="xlsx">XLSX</option>
                    </Select>
                  </label>
                  <Button
                    variant="secondary"
                    loading={isExporting}
                    onClick={async () => {
                      setIsExporting(true);
                      try {
                        await api.exportProject(project.id, exportFormat as "csv" | "json" | "xlsx");
                        toast.success("Export downloaded");
                      } catch (err) {
                        toast.error(err instanceof Error ? err.message : "Export failed");
                      } finally {
                        setIsExporting(false);
                      }
                    }}
                  >
                    <Download className="h-4 w-4" />
                    Download
                  </Button>
                </div>
              ) : null}
            </div>
            <PaginatedResultsTable
              projectId={projectId}
              specFields={project.spec?.fields}
              isCompleted={isCompleted}
            />
          </section>

          {/* Raw Debug Data */}
          <section className="rounded-lg border border-line bg-surface p-6 shadow-panel">
            <button
              type="button"
              className="text-xs text-muted/70 transition hover:text-muted"
              onClick={() => setShowDeveloper((value) => !value)}
            >
              {showDeveloper ? "Hide raw debug data" : "Show raw debug data"}
            </button>
            {showDeveloper ? (
              <>
              <p className="mt-1 text-xs text-muted/60">Technical details for debugging or support. Not needed for normal use.</p>
              <pre className="mt-4 overflow-x-auto rounded-lg border border-line bg-porcelain p-4 text-xs text-ink">
                {JSON.stringify(
                  {
                    system_state: project.system_state,
                    render_mode: project.render_mode,
                    workflow_mode: project.workflow_mode,
                    fetch_metadata: project.fetch_metadata,
                    analysis: project.analysis,
                    spec: project.spec,
                    frontier_preview: project.frontier_preview,
                  },
                  null,
                  2
                )}
              </pre>
              </>
            ) : null}
          </section>
        </div>
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
