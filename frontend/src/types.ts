export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
};

export type UserResponse = {
  id: number;
  email: string;
  is_active: boolean;
  is_verified: boolean;
  default_provider_id: number | null;
};

export type AuthResponse = {
  user: UserResponse;
  tokens: TokenResponse;
};

export type ProviderConfig = {
  id: number;
  name: string;
  provider: string;
  model: string;
  is_default: boolean;
  capability_flags: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ProviderCreateInput = {
  name: string;
  provider: string;
  model: string;
  api_key: string;
  is_default?: boolean;
};

export type ProviderUpdateInput = Partial<ProviderCreateInput>;

export type ProviderKeyRevealInput = {
  password: string;
};

export type ProviderTestResponse = {
  ok: boolean;
  provider_config_id: number;
  capability_flags: Record<string, unknown>;
  error: string | null;
};

export type TaskState =
  | "PERMISSION_GRANTED"
  | "SCRAPING"
  | "SCRAPED"
  | "LLM_PROCESSING"
  | "COMPLETED"
  | "FAILED"
  | string;

export type TaskResponse = {
  task_id: number;
  state: TaskState;
  url: string;
  error: string | null;
  result: Record<string, unknown> | null;
  message: string | null;
  created_at: string | null;
  content_length: number | null;
};

export type ProviderKeyResponse = {
  api_key: string;
};

export type HealthResponse = Record<string, unknown>;

// ---------------------------------------------------------------------------
// Jobs (Phase 1 — Analysis pipeline)
// ---------------------------------------------------------------------------

export type JobState =
  | "QUEUED"
  | "ANALYZING"
  | "AWAITING_SETUP"
  | "ANALYSIS_READY"
  | "FAILED"
  | "CANCELED"
  | string;

export type ExtractionMode = "STRUCTURED" | "CONTENT";
export type WorkflowMode = "GUIDED" | "FAST";
export type RenderMode = "AUTO" | "STATIC" | "BROWSER";

export type JobCreateInput = {
  url: string;
  extraction_mode?: ExtractionMode;
  workflow_mode?: WorkflowMode;
  render_mode?: RenderMode;
  provider_config_id?: number | null;
};

export type JobListItem = {
  id: number;
  url: string;
  state: JobState;
  extraction_mode: ExtractionMode;
  workflow_mode: WorkflowMode;
  render_mode: RenderMode;
  confidence: number | null;
  warnings: string[];
  error: string | null;
  error_code: string | null;
  created_at: string;
};

export type StructuredCandidateField = {
  name: string;
  label: string;
  selector: string;
  data_type: string;
  required: boolean;
  confidence: number;
  sample_values: string[];
};

export type StructuredAnalysis = {
  page_type: string;
  repeated_item_selector: string;
  candidate_fields: StructuredCandidateField[];
  detail_link_selector: string | null;
  pagination_selector: string | null;
  estimated_pages: number | null;
  warnings: string[];
  confidence: number;
};

export type ContentMetadataField = {
  name: string;
  label: string;
  selector: string;
  confidence: number;
  sample_values: string[];
};

export type ContentAnalysis = {
  content_type: string;
  primary_content_selector: string;
  estimated_pages: number | null;
  avg_content_length: number | null;
  recommended_chunking: string;
  metadata_fields: ContentMetadataField[];
  warnings: string[];
  confidence: number;
};

export type JobResponse = {
  id: number;
  url: string;
  state: JobState;
  extraction_mode: ExtractionMode;
  workflow_mode: WorkflowMode;
  render_mode: RenderMode;
  provider_config_id: number | null;
  confidence: number | null;
  warnings: string[];
  analysis:
    | StructuredAnalysis
    | ContentAnalysis
    | Record<string, unknown>
    | null;
  fetch_metadata: Record<string, unknown> | null;
  error: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string | null;
};

// ---------------------------------------------------------------------------
// Projects (Project → Analyze → Fields → Preview → Extract → Results)
// ---------------------------------------------------------------------------

export type ProjectState =
  | "QUEUED"
  | "ANALYZING"
  | "AWAITING_SETUP"
  | "ANALYSIS_READY"
  | "PREVIEWING"
  | "PREVIEW_READY"
  | "DISCOVERING"
  | "EXTRACTING"
  | "EXPORTING"
  | "COMPLETED"
  | "PAUSED"
  | "FAILED"
  | "CANCELED"
  | string;

export type ProjectAnalyzeInput = {
  url: string;
  advanced?: {
    extraction_mode?: ExtractionMode;
    workflow_mode?: WorkflowMode;
    render_mode?: RenderMode;
    provider_config_id?: number | null;
  };
};

export type FieldSpec = {
  name: string | null;
  label: string | null;
  user_label: string | null;
  selector: string | null;
  type: string;
  selected: boolean;
  required: boolean;
  confidence: number | null;
  sample_values: string[];
  warnings: string[];
};

// ---------------------------------------------------------------------------
// Crawl Scope (Phase 2.5)
// ---------------------------------------------------------------------------

export type CrawlScopeMode =
  | "CURRENT_PAGE"
  | "PAGINATION"
  | "DATASET"
  | "FULL_SITE";
export type CrawlScopeStatus =
  | "AI_SUGGESTED"
  | "USER_CONFIRMED"
  | "SYSTEM_DEFAULTED";

export type CrawlScopePagination = {
  selector: string | null;
  url_pattern: string | null;
  estimated_pages: number | null;
};

export type CrawlScopeLinkRule = {
  role: string;
  action: string;
  selector: string | null;
  pattern: string | null;
  reason: string | null;
  confidence: number | null;
};

export type CrawlScopeAiRecommendation = {
  recommended_mode: CrawlScopeMode;
  confidence: number;
  warnings: string[];
  evidence: string[];
};

export type CrawlScope = {
  version: number;
  mode: CrawlScopeMode;
  status: CrawlScopeStatus;
  seed_url: string | null;
  max_pages: number;
  max_depth: number | null;
  include_patterns: string[];
  exclude_patterns: string[];
  pagination: CrawlScopePagination;
  link_rules: CrawlScopeLinkRule[];
  ai_recommendation: CrawlScopeAiRecommendation | null;
  user_confirmed_at: string | null;
};

// ---------------------------------------------------------------------------
// Frontier Preview (Phase 2.5)
// ---------------------------------------------------------------------------

export type FrontierUrlDecision = {
  url: string;
  normalized_url: string;
  source_url: string | null;
  depth: number;
  decision: "included" | "excluded";
  role: string | null;
  reason_code: string;
  reason: string;
  confidence: number | null;
  link_text: string | null;
};

export type FrontierPreviewResponse = {
  id: number;
  project_id: number;
  spec_id: number;
  scope_hash: string;
  included_urls: FrontierUrlDecision[];
  excluded_urls: FrontierUrlDecision[];
  estimated_page_count: number | null;
  warnings: Record<string, unknown>[];
  quality_summary: Record<string, unknown>;
  created_at: string | null;
};

// ---------------------------------------------------------------------------
// Extraction Quality (Phase 2.5)
// ---------------------------------------------------------------------------

export type ExtractionQuality = {
  overall: "good" | "needs_review" | "risky" | "unknown" | string;
  field_success_rates: Record<string, number>;
  missing_field_rates: Record<string, number>;
  warnings: Record<string, unknown>[];
};

// ---------------------------------------------------------------------------
// Records Pagination (Phase 2.5)
// ---------------------------------------------------------------------------

export type RecordPageResponse = {
  items: ProjectRecord[];
  total: number;
  skip: number;
  limit: number;
  next_skip: number | null;
  has_more: boolean;
  columns: string[];
};

export type ExtractionSpecResponse = {
  id: number;
  project_id: number;
  mode: ExtractionMode;
  fields: FieldSpec[];
  content_config: Record<string, unknown>;
  url_patterns: Record<string, unknown>[];
  page_limit: number;
  export_format: "csv" | "json" | "xlsx" | string;
  crawl_scope: CrawlScope | null;
  quality_summary: Record<string, unknown> | null;
  created_at: string;
  updated_at: string | null;
};

export type PreviewResponse = {
  id: number;
  project_id: number;
  spec_id: number;
  sample_records: Record<string, unknown>[];
  warnings: unknown[];
  missing_fields: unknown[];
  quality_summary: Record<string, unknown>;
  created_at: string;
};

export type ExtractionProgress = {
  crawl_pages_total: number;
  crawl_pages_pending: number;
  crawl_pages_fetching: number;
  crawl_pages_extracted: number;
  crawl_pages_blocked: number;
  crawl_pages_failed: number;
  extracted_records_total: number;
  exports_total: number;
};

export type ProjectListItem = {
  id: number;
  url: string;
  system_state: ProjectState;
  product_status: string;
  product_status_label: string;
  product_status_tone: string;
  detected_type: string | null;
  confidence: number | null;
  confidence_label: string;
  selected_field_count: number;
  extraction_mode: ExtractionMode;
  last_activity: string | null;
  error: string | null;
  error_code: string | null;
};

export type ProjectResponse = ProjectListItem & {
  workflow_mode: WorkflowMode;
  render_mode: RenderMode;
  provider_config_id: number | null;
  warnings: string[];
  analysis:
    | StructuredAnalysis
    | ContentAnalysis
    | Record<string, unknown>
    | null;
  fetch_metadata: Record<string, unknown> | null;
  spec: ExtractionSpecResponse | null;
  preview: PreviewResponse | null;
  frontier_preview: FrontierPreviewResponse | null;
  extraction_quality: ExtractionQuality | null;
  preview_stale: boolean;
  progress: ExtractionProgress;
  created_at: string;
  updated_at: string | null;
};

export type ProjectRecord = {
  id: number;
  source_url: string;
  raw_data: Record<string, unknown>;
  normalized_data: Record<string, unknown> | null;
  warnings: unknown[];
  created_at: string;
};
