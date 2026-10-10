"use client";

import clsx from "clsx";
import { useState } from "react";
import { useGraph, useWorkflows } from "@/lib/api/hooks";
import type { WorkflowStatus } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { Button, Card, CardHeader, ErrorState, LoadingRows, PageHeader, Skeleton } from "@/components/ui/primitives";
import { NewRequest } from "@/components/workflows/new-request";
import { WorkflowGraph } from "@/components/workflows/workflow-graph";
import { WorkflowTable } from "@/components/workflows/workflow-table";

const STAGES_BEFORE_ROUTING = ["__start__", "classify", "retrieve_context", "plan", "dispatch"];
const STATUSES: (WorkflowStatus | "all")[] = ["all", "running", "waiting_for_approval", "completed", "failed", "cancelled"];
const PAGE = 20;

const INTENT_HELP: Record<string, string> = {
  bug_investigation: "Diagnose a failure, propose a minimal fix, review and verify it, then open a draft PR after approval.",
  feature_planning: "Decompose the request into a dependency-ordered plan and file a tracking issue after approval.",
  code_review: "Review a pull request's diff and post the findings as a comment after approval.",
  repository_analysis: "Summarize structure, dependencies and entry points; store the summary in long-term memory.",
};

export function WorkflowsView() {
  const { data: topology, error: graphError } = useGraph();
  const [intent, setIntent] = useState<string>("bug_investigation");
  const [status, setStatus] = useState<WorkflowStatus | "all">("all");
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading, mutate } = useWorkflows({ status: status === "all" ? undefined : status, limit: PAGE, offset });
  const route = topology ? [...STAGES_BEFORE_ROUTING, ...(topology.routes[intent] ?? []), "report", "__end__"] : [];

  return (
    <>
      <PageHeader
        title="Workflows"
        description="The LangGraph workflow every request runs through. Pick an intent to see the stages it routes to; dashed edges are conditional."
      />
      <Card className="mb-4">
        <CardHeader
          title="Workflow graph"
          description={INTENT_HELP[intent]}
          actions={
            <div role="radiogroup" aria-label="Intent route" className="flex flex-wrap gap-1">
              {Object.keys(topology?.routes ?? INTENT_HELP).map((k) => (
                <button
                  key={k}
                  role="radio"
                  type="button"
                  aria-checked={intent === k}
                  onClick={() => setIntent(k)}
                  className={clsx("rounded px-2 py-1 text-xs", intent === k ? "bg-accent-soft text-accent" : "text-muted hover:text-text")}
                >
                  {humanize(k)}
                </button>
              ))}
            </div>
          }
        />
        {graphError ? <ErrorState error={graphError} /> : topology ? <WorkflowGraph topology={topology} route={route} height={360} /> : <Skeleton className="h-[360px]" />}
        <div className="grid gap-2 border-t border-border px-4 py-3 text-xs text-muted md:grid-cols-3">
          <p><span className="text-text">Limits.</span> Each run has a step limit and a wall-clock timeout; the verify → debug loop is bounded by the revision limit.</p>
          <p><span className="text-text">Retries.</span> Rate limits and network errors are retried with exponential backoff; permanent errors fail the stage.</p>
          <p><span className="text-text">Approval.</span> The approval gate checkpoints the run and releases nothing until every proposed action is decided.</p>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
        <Card>
          <CardHeader
            title="Execution history"
            description={data ? `${data.total} workflows` : undefined}
            actions={
              <select
                aria-label="Filter by status"
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value as WorkflowStatus | "all");
                  setOffset(0);
                }}
                className="h-7 rounded-md border border-border bg-panel-2 px-2 text-xs text-text"
              >
                {STATUSES.map((s) => <option key={s} value={s}>{s === "all" ? "All statuses" : humanize(s)}</option>)}
              </select>
            }
          />
          {error ? <ErrorState error={error} onRetry={() => mutate()} /> : isLoading ? <LoadingRows rows={6} /> : <WorkflowTable workflows={data?.items ?? []} />}
          {data && data.total > PAGE && (
            <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-2 text-xs text-muted">
              <span>{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</span>
              <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button size="sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </div>
          )}
        </Card>
        <div>
          <NewRequest compact />
        </div>
      </div>
    </>
  );
}
