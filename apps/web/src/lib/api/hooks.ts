"use client";

import useSWR, { type SWRConfiguration } from "swr";
import { api } from "./client";
import { TERMINAL_STATUSES, type WorkflowStatus } from "./types";

const live: SWRConfiguration = { refreshInterval: 4000, revalidateOnFocus: true };

export const useHealth = () => useSWR("health", api.health, { refreshInterval: 15000 });
export const useMe = () => useSWR("me", api.me, { revalidateOnFocus: false });
export const useMetrics = () => useSWR("metrics", api.metrics, live);
export const useAgents = () => useSWR("agents", api.agents, live);
export const useTools = () => useSWR("tools", api.tools);
export const useGraph = () => useSWR("graph", api.graph, { revalidateOnFocus: false });
export const useRepositories = () => useSWR("repositories", api.repositories.list);
export const useSettings = () => useSWR("settings", api.settings.get);
export const useGitHubStatus = () => useSWR("github-status", api.github.status);

export function useWorkflows(query?: { status?: WorkflowStatus; repository_id?: string; limit?: number; offset?: number }) {
  return useSWR(["workflows", JSON.stringify(query ?? {})], () => api.workflows.list(query), live);
}

export function useWorkflow(id: string) {
  return useSWR(["workflow", id], () => api.workflows.get(id), {
    refreshInterval: (data) => (data && TERMINAL_STATUSES.includes(data.status as WorkflowStatus) ? 0 : 2500),
  });
}

export function useToolCalls(id: string, active: boolean) {
  return useSWR(["tool-calls", id], () => api.workflows.toolCalls(id), { refreshInterval: active ? 3000 : 0 });
}

export function useApprovals(status?: string, workflowId?: string) {
  return useSWR(["approvals", status ?? "", workflowId ?? ""], () =>
    api.approvals.list({ status, workflow_id: workflowId, limit: 100 }), live);
}

export const useRepository = (id: string) => useSWR(["repository", id], () => api.repositories.get(id));
export const useRepositoryTree = (id: string) => useSWR(["repository-tree", id], () => api.repositories.tree(id));
export const useRepositoryIssues = (id: string) => useSWR(["repository-issues", id], () => api.repositories.issues(id));
export const useRepositoryPulls = (id: string) => useSWR(["repository-pulls", id], () => api.repositories.pulls(id));
export const useRepositoryCI = (id: string) => useSWR(["repository-ci", id], () => api.repositories.ci(id));
export const useRepositoryFile = (id: string, path: string | null) =>
  useSWR(path ? ["repository-file", id, path] : null, () => api.repositories.file(id, path as string));
