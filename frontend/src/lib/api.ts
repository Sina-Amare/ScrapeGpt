import {
  AuthResponse,
  HealthResponse,
  ExtractionSpecResponse,
  FieldSpec,
  JobCreateInput,
  JobListItem,
  JobResponse,
  ProjectAnalyzeInput,
  ProjectListItem,
  ProjectRecord,
  ProjectResponse,
  ProviderConfig,
  ProviderCreateInput,
  ProviderKeyRevealInput,
  ProviderKeyResponse,
  ProviderTestResponse,
  ProviderUpdateInput,
  TaskResponse,
  TokenResponse
} from "../types";
import {
  clearStoredRefreshToken,
  getStoredRefreshToken,
  setStoredRefreshToken
} from "./storage";

const viteEnv = (import.meta as ImportMeta & { env?: { VITE_API_BASE_URL?: string } })
  .env;
const apiBaseUrl = viteEnv?.VITE_API_BASE_URL ?? "/api/v1";

let accessToken: string | null = null;
let authFailureHandler: (() => void) | null = null;
let refreshPromise: Promise<TokenResponse> | null = null;

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(extractErrorMessage(detail, status));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAuthFailureHandler(handler: (() => void) | null): void {
  authFailureHandler = handler;
}

function extractErrorMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (
    detail &&
    typeof detail === "object" &&
    "detail" in detail &&
    typeof detail.detail === "string"
  ) {
    return detail.detail;
  }
  if (
    detail &&
    typeof detail === "object" &&
    "detail" in detail &&
    detail.detail &&
    typeof detail.detail === "object" &&
    "message" in detail.detail &&
    typeof detail.detail.message === "string"
  ) {
    return detail.detail.message;
  }
  return `Request failed with status ${status}`;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as T;
}

async function rawRequest<T>(
  path: string,
  init: RequestInit = {},
  includeAuth = true
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (includeAuth && accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers
  });

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await parseResponse<unknown>(response);
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }

  return parseResponse<T>(response);
}

async function refreshAccessToken(): Promise<TokenResponse> {
  if (refreshPromise) return refreshPromise;
  const refreshToken = getStoredRefreshToken();
  if (!refreshToken) throw new ApiError(401, "Missing refresh token");

  refreshPromise = rawRequest<TokenResponse>(
    "/auth/refresh",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken })
    },
    false
  )
    .then((tokens) => {
      setAccessToken(tokens.access_token);
      setStoredRefreshToken(tokens.refresh_token);
      return tokens;
    })
    .finally(() => {
      refreshPromise = null;
    });

  return refreshPromise;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  retryOnUnauthorized = true
): Promise<T> {
  try {
    return await rawRequest<T>(path, init, true);
  } catch (error) {
    if (
      retryOnUnauthorized &&
      error instanceof ApiError &&
      error.status === 401 &&
      getStoredRefreshToken()
    ) {
      try {
        await refreshAccessToken();
        return await rawRequest<T>(path, init, true);
      } catch (refreshError) {
        clearStoredRefreshToken();
        setAccessToken(null);
        authFailureHandler?.();
        throw refreshError;
      }
    }
    throw error;
  }
}

export const api = {
  async register(email: string, password: string): Promise<AuthResponse> {
    return rawRequest<AuthResponse>(
      "/auth/register",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      },
      false
    );
  },

  async login(email: string, password: string): Promise<TokenResponse> {
    const body = new URLSearchParams();
    body.set("username", email);
    body.set("password", password);
    return rawRequest<TokenResponse>(
      "/auth/login",
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body
      },
      false
    );
  },

  refreshAccessToken,

  getHealth(path: "/health" | "/health/live" | "/health/ready"): Promise<HealthResponse> {
    return rawRequest<HealthResponse>(path, {}, false);
  },

  listProviders(): Promise<ProviderConfig[]> {
    return apiRequest<ProviderConfig[]>("/providers");
  },

  createProvider(input: ProviderCreateInput): Promise<ProviderConfig> {
    return apiRequest<ProviderConfig>("/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  updateProvider(id: number, input: ProviderUpdateInput): Promise<ProviderConfig> {
    return apiRequest<ProviderConfig>(`/providers/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  deleteProvider(id: number): Promise<void> {
    return apiRequest<void>(`/providers/${id}`, { method: "DELETE" });
  },

  testProvider(id: number): Promise<ProviderTestResponse> {
    return apiRequest<ProviderTestResponse>(`/providers/${id}/test`, {
      method: "POST"
    });
  },

  startScrape(url: string): Promise<TaskResponse> {
    return apiRequest<TaskResponse>("/scrape/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
  },

  getCurrentTask(): Promise<TaskResponse> {
    return apiRequest<TaskResponse>("/scrape/tasks/current");
  },

  getTask(taskId: number): Promise<TaskResponse> {
    return apiRequest<TaskResponse>(`/scrape/tasks/${taskId}`);
  },

  listTasks(): Promise<TaskResponse[]> {
    return apiRequest<TaskResponse[]>("/scrape/tasks");
  },

  deleteTask(taskId: number): Promise<void> {
    return apiRequest<void>(`/scrape/tasks/${taskId}`, {
      method: "DELETE"
    });
  },

  revealProviderKey(id: number, input: ProviderKeyRevealInput): Promise<ProviderKeyResponse> {
    return apiRequest<ProviderKeyResponse>(`/providers/${id}/reveal-key`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  // -------------------------------------------------------------------------
  // Jobs (Phase 1 — Analysis pipeline)
  // -------------------------------------------------------------------------

  createJob(input: JobCreateInput): Promise<JobResponse> {
    return apiRequest<JobResponse>("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  listJobs(limit = 50): Promise<JobListItem[]> {
    return apiRequest<JobListItem[]>(`/jobs?limit=${limit}`);
  },

  getJob(id: number): Promise<JobResponse> {
    return apiRequest<JobResponse>(`/jobs/${id}`);
  },

  cancelJob(id: number): Promise<JobResponse> {
    return apiRequest<JobResponse>(`/jobs/${id}/cancel`, { method: "POST" });
  },

  deleteJob(id: number): Promise<void> {
    return apiRequest<void>(`/jobs/${id}`, { method: "DELETE" });
  },

  // -------------------------------------------------------------------------
  // Projects (primary workflow)
  // -------------------------------------------------------------------------

  analyzeProject(input: ProjectAnalyzeInput): Promise<ProjectResponse> {
    return apiRequest<ProjectResponse>("/projects/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  listProjects(limit = 50): Promise<ProjectListItem[]> {
    return apiRequest<ProjectListItem[]>(`/projects?limit=${limit}`);
  },

  getProject(id: number): Promise<ProjectResponse> {
    return apiRequest<ProjectResponse>(`/projects/${id}`);
  },

  updateProjectSpec(
    id: number,
    input: {
      fields?: FieldSpec[];
      content_config?: Record<string, unknown>;
      url_patterns?: Record<string, unknown>[];
      page_limit?: number;
      export_format?: string;
    }
  ): Promise<ExtractionSpecResponse> {
    return apiRequest<ExtractionSpecResponse>(`/projects/${id}/spec`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  previewProject(id: number): Promise<ProjectResponse["preview"]> {
    return apiRequest<ProjectResponse["preview"]>(`/projects/${id}/preview`, {
      method: "POST"
    });
  },

  extractProject(id: number, extractAnyway = false): Promise<ProjectResponse> {
    return apiRequest<ProjectResponse>(`/projects/${id}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ extract_anyway: extractAnyway })
    });
  },

  listProjectRecords(id: number, limit = 100): Promise<ProjectRecord[]> {
    return apiRequest<ProjectRecord[]>(`/projects/${id}/records?limit=${limit}`);
  },

  cancelProject(id: number): Promise<ProjectResponse> {
    return apiRequest<ProjectResponse>(`/projects/${id}/cancel`, { method: "POST" });
  },

  deleteProject(id: number): Promise<void> {
    return apiRequest<void>(`/projects/${id}`, { method: "DELETE" });
  }
};
