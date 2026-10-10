/**
 * Typed client for the Orchestra API.
 *
 * The browser only talks to this app's own `/api/orchestra/*` route handler, which
 * attaches the API token server-side. Tokens never reach the browser.
 */
import type {
  Agent,
  Approval,
  CIStatus,
  DeviceStart,
  FileContent,
  GitHubStatus,
  GraphTopology,
  Health,
  Issue,
  Me,
  Metrics,
  Page,
  Pull,
  RepoInfo,
  Repository,
  RepositoryConnect,
  RepositoryTree,
  SettingsOut,
  SettingsUpdate,
  ToolCall,
  ToolsResponse,
  WorkflowCreate,
  WorkflowDetail,
  WorkflowEvent,
  WorkflowStatus,
  WorkflowSummary,
} from "./types";

export const API_BASE = "/api/orchestra";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: unknown,
    readonly requestId: string | null,
  ) {
    super(ApiError.describe(status, detail));
    this.name = "ApiError";
  }

  static describe(status: number, detail: unknown): string {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { loc?: unknown[]; msg?: string };
      const field = first.loc?.filter((p) => p !== "body").join(".");
      return field ? `${field}: ${first.msg}` : (first.msg ?? `Request failed (${status})`);
    }
    if (status === 0) return "Cannot reach the Orchestra API";
    return `Request failed (${status})`;
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

function qs(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  const s = params.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(path: string, init?: RequestInit & { query?: Query }): Promise<T> {
  const { query, ...rest } = init ?? {};
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}${qs(query)}`, {
      ...rest,
      headers: { Accept: "application/json", ...(rest.body ? { "Content-Type": "application/json" } : {}), ...rest.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network_error", null, null);
  }
  if (!res.ok) {
    let body: { error?: string; detail?: unknown; request_id?: string } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, body.error ?? "error", body.detail ?? null, body.request_id ?? res.headers.get("x-request-id"));
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  me: () => request<Me>("/me"),
  metrics: () => request<Metrics>("/metrics"),
  agents: () => request<Agent[]>("/agents"),
  tools: () => request<ToolsResponse>("/tools"),
  graph: () => request<GraphTopology>("/workflow-graph"),

  workflows: {
    list: (query?: { status?: WorkflowStatus; repository_id?: string; limit?: number; offset?: number }) =>
      request<Page<WorkflowSummary>>("/workflows", { query }),
    get: (id: string) => request<WorkflowDetail>(`/workflows/${encodeURIComponent(id)}`),
    create: (body: WorkflowCreate) => post<WorkflowSummary>("/workflows", body),
    cancel: (id: string) => post<WorkflowSummary>(`/workflows/${encodeURIComponent(id)}/cancel`),
    events: (id: string, after = 0) =>
      request<WorkflowEvent[]>(`/workflows/${encodeURIComponent(id)}/events`, { query: { after } }),
    toolCalls: (id: string) => request<ToolCall[]>(`/workflows/${encodeURIComponent(id)}/tool-calls`),
  },

  repositories: {
    list: () => request<Page<Repository>>("/repositories", { query: { limit: 200 } }),
    get: (id: string) => request<Repository>(`/repositories/${encodeURIComponent(id)}`),
    connect: (body: RepositoryConnect) => post<Repository>("/repositories/connect", body),
    analyze: (id: string) => post<WorkflowSummary>(`/repositories/${encodeURIComponent(id)}/analyze`),
    tree: (id: string) => request<RepositoryTree>(`/repositories/${encodeURIComponent(id)}/tree`),
    file: (id: string, path: string) =>
      request<FileContent>(`/repositories/${encodeURIComponent(id)}/files`, { query: { path } }),
    issues: (id: string) => request<Issue[]>(`/repositories/${encodeURIComponent(id)}/issues`),
    pulls: (id: string) => request<Pull[]>(`/repositories/${encodeURIComponent(id)}/pulls`),
    ci: (id: string) => request<CIStatus>(`/repositories/${encodeURIComponent(id)}/ci`),
  },

  approvals: {
    list: (query?: { status?: string; workflow_id?: string; limit?: number }) =>
      request<Page<Approval>>("/approvals", { query }),
    approve: (id: string, reason?: string) =>
      post<Approval>(`/approvals/${encodeURIComponent(id)}/approve`, reason ? { reason } : {}),
    reject: (id: string, reason: string) => post<Approval>(`/approvals/${encodeURIComponent(id)}/reject`, { reason }),
  },

  settings: {
    get: () => request<SettingsOut>("/settings"),
    update: (body: SettingsUpdate) => request<SettingsOut>("/settings", { method: "PUT", body: JSON.stringify(body) }),
  },

  github: {
    status: () => request<GitHubStatus>("/github/status"),
    repos: () => request<RepoInfo[]>("/github/repos"),
    deviceStart: () => post<DeviceStart>("/github/device/start"),
    devicePoll: (device_code: string) => post<{ status: string }>("/github/device/poll", { device_code }),
  },
};

export function eventStreamUrl(workflowId: string, after: number): string {
  return `${API_BASE}/workflows/${encodeURIComponent(workflowId)}/events${qs({ stream: true, after })}`;
}
