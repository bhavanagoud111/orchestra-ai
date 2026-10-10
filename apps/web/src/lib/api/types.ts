import type { components } from "./schema";

type S = components["schemas"];

export type WorkflowSummary = S["WorkflowSummary"];
export type WorkflowDetail = S["WorkflowDetail"];
export type WorkflowCreate = S["WorkflowCreate"];
export type WorkflowEvent = S["EventOut"];
export type ToolCall = S["ToolCallOut"];
export type Approval = S["ApprovalOut"];
export type Repository = S["RepositoryOut"];
export type RepositoryConnect = S["RepositoryConnect"];
export type FileContent = S["FileContent"];
export type Issue = S["IssueInfo"];
export type Pull = S["PullInfo"];
export type RepoInfo = S["RepoInfo"];
export type Agent = S["AgentOut"];
export type ToolsResponse = S["ToolsResponse"];
export type Tool = S["ToolOut"];
export type Metrics = S["Metrics"];
export type Health = S["Health"];
export type Me = S["Me"];
export type SettingsOut = S["SettingsOut"];
export type SettingsUpdate = S["SettingsUpdate"];
export type EffectiveSettings = S["EffectiveSettings"];

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type WorkflowStatus = "pending" | "running" | "waiting_for_approval" | "completed" | "failed" | "cancelled";
export const TERMINAL_STATUSES: WorkflowStatus[] = ["completed", "failed", "cancelled"];

// Endpoints that return free-form JSON in the OpenAPI schema.
export type GraphTopology = {
  nodes: { id: string; agent: string | null }[];
  edges: { source: string; target: string; conditional: boolean }[];
  routes: Record<string, string[]>;
};

export type RepositoryTree = {
  files: string[];
  languages: Record<string, number>;
  dependencies: string[];
  entry_points: string[];
  docs: string[];
  tests: string[];
};

export type CIStatus = {
  ref: string;
  state: "success" | "failure" | "pending" | "none";
  checks: { name: string; status: string; conclusion: string | null; html_url: string | null }[];
};

export type GitHubStatus = {
  connected: boolean;
  device_flow_available: boolean;
  user?: { login: string; name: string | null; html_url: string | null };
  error?: string;
};

export type DeviceStart = {
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
};

// Structured agent outputs stored in workflow state (see services/api schemas/agents.py).
export type RelevantFile = { path: string; reason: string; score: number; start_line?: number | null; end_line?: number | null };
export type RepositoryContext = {
  summary: string;
  languages: string[];
  dependencies: string[];
  entry_points: string[];
  relevant_files: RelevantFile[];
  code_relationships: string[];
  documentation: string[];
  risks: string[];
};
export type PlannedTask = {
  id: string;
  title: string;
  description: string;
  agent: string;
  depends_on: string[];
  complexity: number;
  acceptance_criteria: string[];
  required_tools: string[];
  required_permissions: string[];
};
export type ExecutionPlan = { summary: string; tasks: PlannedTask[]; risks: string[]; open_questions: string[] };
export type Hypothesis = { root_cause: string; evidence: string[]; confidence: number };
export type FileEdit = { path: string; find: string; replace: string; rationale: string };
export type DebugReport = {
  error_signature: string | null;
  hypotheses: Hypothesis[];
  proposed_fix: { summary: string; edits: FileEdit[]; tests_to_add: string[] } | null;
  needs_more_evidence: string[];
};
export type ReviewFinding = {
  severity: "info" | "low" | "medium" | "high" | "critical";
  category: string;
  path: string | null;
  message: string;
  suggestion: string | null;
};
export type ReviewReport = { verdict: string; summary: string; findings: ReviewFinding[]; test_coverage: string };
export type CheckResult = { name: string; kind: string; status: "passed" | "failed" | "skipped"; required: boolean; details: string };
export type VerificationReport = {
  outcome: "passed" | "failed" | "unverified";
  checks: CheckResult[];
  blocking_failures: string[];
};
export type FinalReport = {
  headline: string;
  outcome: "completed" | "blocked" | "partial" | "failed";
  summary: string;
  completed: string[];
  pending: string[];
  decisions: { agent: string; decision: string; rationale: string }[];
  attention_required: string[];
};
export type IntentClassification = {
  intent: string;
  confidence: number;
  rationale: string;
  issue_number?: number | null;
  pull_number?: number | null;
  search_terms: string[];
};
export type ActionResult = {
  title: string;
  status: "succeeded" | "failed" | "skipped";
  url?: string | null;
  error?: string;
  reason?: string;
  simulated?: boolean;
};

export type WorkflowState = {
  intent?: IntentClassification;
  context?: RepositoryContext;
  plan?: ExecutionPlan;
  route?: string[];
  debug?: DebugReport;
  review?: ReviewReport;
  verification?: VerificationReport;
  report?: FinalReport;
  action_results?: ActionResult[];
  changed_files?: Record<string, string>;
  blocked_reason?: string | null;
  security_findings?: number;
  errors?: string[];
  revisions?: number;
};
