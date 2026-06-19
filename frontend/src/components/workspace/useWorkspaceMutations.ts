import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, api } from "../../lib/api";
import { ACTIVE_PROJECT_STATES } from "../../lib/projectPolling";
import { isUserConfirmed, requiresConfirmation } from "../../lib/scopeCopy";
import { scopeWithSmartDefaults } from "../../lib/scopeDefaults";
import {
  BrowserSession,
  CrawlScope,
  CrawlScopeMode,
  CrawlScopeStatus,
  FieldSpec,
  InteractionProfile,
  ProjectResponse,
  ProjectState,
} from "../../types";

/**
 * Lifts all of the extraction workspace's mutations + local editing state out of
 * the page into one controller. Behavior is a faithful move of the original
 * ProjectDetailPage logic — including the optimistic `preview_stale` flips that
 * the staleness gate depends on. The wizard owns one instance; steps consume it.
 */
export function useWorkspaceMutations(project: ProjectResponse) {
  const projectId = project.id;
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [fields, setFields] = useState<FieldSpec[]>(project.spec?.fields ?? []);
  const [pageLimit, setPageLimit] = useState(project.spec?.page_limit ?? 500);
  const [exportFormat, setExportFormat] = useState(project.spec?.export_format ?? "csv");
  const [showDeveloper, setShowDeveloper] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [retryProviderId, setRetryProviderId] = useState<number | null>(null);

  // Crawl scope draft state.
  const [draftMode, setDraftMode] = useState<CrawlScopeMode | null>(
    project.spec?.crawl_scope?.mode ?? null
  );
  // Stale preview tracking: true when scope was saved after the last preview.
  const [scopeChangedAfterPreview, setScopeChangedAfterPreview] = useState(false);
  // Scope confirmation error from extract 409.
  const [extractScopeError, setExtractScopeError] = useState<string | null>(null);
  // Preview-gate error (NO_PREVIEW / STALE_PREVIEW / ZERO_PREVIEW_RECORDS).
  const [extractGateError, setExtractGateError] = useState<{
    code: string;
    message: string;
  } | null>(null);

  const savedScope = project.spec?.crawl_scope ?? null;

  const effectiveDraftMode: CrawlScopeMode =
    draftMode ?? savedScope?.mode ?? "CURRENT_PAGE";
  const effectiveScope: CrawlScope | null = savedScope
    ? {
        ...savedScope,
        mode: effectiveDraftMode,
        status:
          effectiveDraftMode !== savedScope.mode
            ? ("AI_SUGGESTED" as CrawlScopeStatus)
            : savedScope.status,
        user_confirmed_at:
          effectiveDraftMode !== savedScope.mode ? null : savedScope.user_confirmed_at,
      }
    : null;

  const scopeNeedsConfirmation =
    requiresConfirmation(effectiveDraftMode) &&
    !isUserConfirmed(effectiveScope?.status);

  // Sync local field/scope state when the saved spec changes (e.g. after save).
  useEffect(() => {
    if (project.spec?.fields) {
      setFields(project.spec.fields);
      setPageLimit(project.spec.page_limit);
      setExportFormat(project.spec.export_format);
    }
    if (project.spec?.crawl_scope?.mode && draftMode === null) {
      setDraftMode(project.spec.crawl_scope.mode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    project.spec?.fields,
    project.spec?.id,
    project.spec?.page_limit,
    project.spec?.export_format,
    project.spec?.crawl_scope?.mode,
  ]);

  const saveSpec = useMutation({
    mutationFn: () => api.updateProjectSpec(projectId, { fields, page_limit: pageLimit }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const saveScopeMutation = useMutation({
    mutationFn: (newMode: CrawlScopeMode) =>
      api.updateProjectSpec(projectId, {
        page_limit: pageLimit,
        crawl_scope: scopeWithSmartDefaults(savedScope, newMode),
      }),
    onSuccess: () => {
      setScopeChangedAfterPreview(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const confirmScopeMutation = useMutation({
    mutationFn: () =>
      api.updateProjectSpec(projectId, {
        page_limit: pageLimit,
        crawl_scope: {
          ...scopeWithSmartDefaults(savedScope, effectiveDraftMode),
          status: "USER_CONFIRMED" as CrawlScopeStatus,
          user_confirmed_at: new Date().toISOString(),
        },
      }),
    onSuccess: () => {
      setExtractScopeError(null);
      setScopeChangedAfterPreview(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const frontierPreviewMutation = useMutation({
    mutationFn: () => api.createFrontierPreview(projectId),
    onSuccess: () => {
      setScopeChangedAfterPreview(false);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const broadenScopeMutation = useMutation({
    mutationFn: async (vars: { mode: CrawlScopeMode; includePatterns: string[] }) => {
      await api.updateProjectSpec(projectId, {
        crawl_scope: {
          ...(savedScope ?? {}),
          mode: vars.mode,
          include_patterns: vars.includePatterns,
          status: "USER_CONFIRMED" as CrawlScopeStatus,
          user_confirmed_at: new Date().toISOString(),
        } as Partial<CrawlScope>,
      });
      return api.createFrontierPreview(projectId);
    },
    onSuccess: (_data, vars) => {
      setDraftMode(vars.mode);
      setScopeChangedAfterPreview(false);
      setExtractScopeError(null);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const detectInteractionsMutation = useMutation({
    mutationFn: () => api.detectInteractions(projectId),
    onSuccess: (spec) => {
      setFields(spec.fields);
      setPageLimit(spec.page_limit);
      setExportFormat(spec.export_format);
      queryClient.setQueryData<ProjectResponse | undefined>(
        ["project", projectId],
        (current) =>
          current
            ? {
                ...current,
                spec,
                preview_stale: current.preview ? true : current.preview_stale,
                selected_field_count: spec.fields.filter((field) => field.selected).length,
              }
            : current
      );
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const saveInteractionsMutation = useMutation({
    mutationFn: (next: InteractionProfile) =>
      api.updateProjectSpec(projectId, { interaction_profile: next }),
    onSuccess: (spec) => {
      queryClient.setQueryData<ProjectResponse | undefined>(
        ["project", projectId],
        (current) =>
          current
            ? {
                ...current,
                spec,
                preview_stale: current.preview ? true : current.preview_stale,
              }
            : current
      );
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const savePatternsMutation = useMutation({
    mutationFn: (vars: { include: string[]; exclude: string[] }) =>
      api.updateProjectSpec(projectId, {
        page_limit: pageLimit,
        crawl_scope: {
          ...scopeWithSmartDefaults(savedScope, effectiveDraftMode),
          include_patterns: vars.include,
          exclude_patterns: vars.exclude,
          status: "AI_SUGGESTED" as CrawlScopeStatus,
          user_confirmed_at: null,
        } as Partial<CrawlScope>,
      }),
    onSuccess: () => {
      setScopeChangedAfterPreview(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: (providerConfigId?: number | null) =>
      api.retryProject(projectId, providerConfigId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  // Clear a stale retry error once the project is no longer FAILED.
  const projectState = project.system_state;
  useEffect(() => {
    if (projectState && projectState !== "FAILED") {
      retryMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectState]);

  const siblingMutation = useMutation({
    mutationFn: (vars: { url: string; mode: "STRUCTURED" | "CONTENT" }) =>
      api.analyzeProject({ url: vars.url, advanced: { extraction_mode: vars.mode } }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success("Started a separate project for the other data type");
      navigate(`/projects/${created.id}`);
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Could not start the sibling project"),
  });

  const setSessionMutation = useMutation({
    mutationFn: (sessionId: number | null) => api.setProjectSession(projectId, sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const { data: sessions } = useQuery<BrowserSession[]>({
    queryKey: ["sessions"],
    queryFn: () => api.listSessions(),
  });

  const previewMutation = useMutation({
    mutationFn: async () => {
      const spec = await api.updateProjectSpec(projectId, { fields, page_limit: pageLimit });
      const preview = await api.previewProject(projectId);
      return { spec, preview };
    },
    onSuccess: ({ spec, preview }) => {
      queryClient.setQueryData<ProjectResponse | undefined>(
        ["project", projectId],
        (current) =>
          current
            ? {
                ...current,
                spec,
                preview,
                preview_stale: false,
                selected_field_count: spec.fields.filter((field) => field.selected).length,
              }
            : current
      );
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const extractMutation = useMutation({
    mutationFn: (extractAnyway: boolean) => api.extractProject(projectId, extractAnyway),
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
          setExtractScopeError("Confirm what ScrapeGPT should crawl before extraction.");
          return;
        }
        if (code === "STALE_PREVIEW" || code === "ZERO_PREVIEW_RECORDS" || code === "NO_PREVIEW") {
          setExtractGateError({ code, message: message ?? "Run preview before extracting." });
          return;
        }
      }
    },
  });

  const saveScopeAndContinueMutation = useMutation({
    mutationFn: () => {
      const nextScope = scopeWithSmartDefaults(savedScope, effectiveDraftMode);
      const needsConfirm = requiresConfirmation(effectiveDraftMode);
      return api.updateProjectSpec(projectId, {
        page_limit: pageLimit,
        crawl_scope: {
          ...nextScope,
          status: needsConfirm
            ? ("USER_CONFIRMED" as CrawlScopeStatus)
            : (nextScope.status ?? "SYSTEM_DEFAULTED"),
          user_confirmed_at: needsConfirm
            ? new Date().toISOString()
            : (nextScope.user_confirmed_at ?? null),
        },
      });
    },
    onSuccess: () => {
      setExtractScopeError(null);
      setScopeChangedAfterPreview(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  function handleModeChange(mode: CrawlScopeMode) {
    setDraftMode(mode);
    // CURRENT_PAGE saves immediately; broad modes wait for the confirm click.
    if (mode === "CURRENT_PAGE") {
      saveScopeMutation.mutate(mode);
    }
  }

  function handleConfirmScope() {
    confirmScopeMutation.mutate();
  }

  async function saveScopeAndContinue(): Promise<void> {
    await saveScopeAndContinueMutation.mutateAsync();
  }

  const isCompleted = project.system_state === "COMPLETED";
  const isActive = ACTIVE_PROJECT_STATES.has(project.system_state);
  const isExtracting = (
    ["DISCOVERING", "EXTRACTING", "EXPORTING"] as ProjectState[]
  ).includes(project.system_state);
  const hasRecords = (project.progress?.extracted_records_total ?? 0) > 0;
  const isSavingScope =
    saveScopeMutation.isPending ||
    confirmScopeMutation.isPending ||
    savePatternsMutation.isPending ||
    saveScopeAndContinueMutation.isPending;

  return {
    projectId,
    // local editing state
    fields,
    setFields,
    pageLimit,
    setPageLimit,
    exportFormat,
    setExportFormat,
    showDeveloper,
    setShowDeveloper,
    isExporting,
    setIsExporting,
    retryProviderId,
    setRetryProviderId,
    // scope
    draftMode,
    savedScope,
    effectiveScope,
    effectiveDraftMode,
    scopeNeedsConfirmation,
    scopeChangedAfterPreview,
    handleModeChange,
    handleConfirmScope,
    saveScopeAndContinue,
    isSavingScope,
    // extract gate errors
    extractScopeError,
    setExtractScopeError,
    extractGateError,
    setExtractGateError,
    // mutations
    saveSpec,
    saveScopeMutation,
    confirmScopeMutation,
    saveScopeAndContinueMutation,
    frontierPreviewMutation,
    broadenScopeMutation,
    detectInteractionsMutation,
    saveInteractionsMutation,
    savePatternsMutation,
    retryMutation,
    siblingMutation,
    setSessionMutation,
    previewMutation,
    extractMutation,
    // misc
    sessions,
    isCompleted,
    isActive,
    isExtracting,
    hasRecords,
  };
}

export type WorkspaceController = ReturnType<typeof useWorkspaceMutations>;
