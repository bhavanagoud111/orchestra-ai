import type { Approval, WorkflowEvent } from "@/lib/api/types";

export const approval = (over: Partial<Approval> = {}): Approval => ({
  id: "ap1",
  workflow_id: "wf1",
  action_type: "open_pull_request",
  title: "fix: guard empty carts",
  description: "Create branch, commit 1 file, open a draft PR.",
  risk_level: "medium",
  risk_reasons: ["open pull request writes to GitHub", "Tests were not executed in the sandbox"],
  requested_permissions: ["contents:write on orchestra/* branch"],
  proposed_changes: { branch: "orchestra/fix-12", diffs: { "a.py": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n" }, pr_title: "fix" },
  status: "pending",
  decided_by: null,
  decision_reason: null,
  simulated: true,
  created_at: new Date().toISOString(),
  decided_at: null,
  ...over,
});

export const event = (seq: number, type: string, message: string, over: Partial<WorkflowEvent> = {}): WorkflowEvent => ({
  id: seq,
  workflow_id: "wf1",
  seq,
  type,
  node: "debug",
  agent: "debugging",
  message,
  payload: null,
  duration_ms: null,
  simulated: false,
  created_at: "2026-10-10T12:00:00Z",
  ...over,
});

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}
