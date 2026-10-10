"use client";

import { Ban } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { api } from "@/lib/api/client";
import { useGraph, useToolCalls, useWorkflow } from "@/lib/api/hooks";
import { TERMINAL_STATUSES, type WorkflowState, type WorkflowStatus } from "@/lib/api/types";
import { duration, humanize, relativeTime } from "@/lib/format";
import { useEventStream } from "@/lib/use-event-stream";
import { ApprovalCard } from "@/components/approvals/approval-card";
import { DiffView } from "@/components/ui/diff-view";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, LoadingRows, Mono, Skeleton } from "@/components/ui/primitives";
import { SimulatedBadge, StatusBadge } from "@/components/ui/status";
import { Tabs, type TabItem } from "@/components/ui/tabs";
import {
  ContextPanel,
  DebugPanel,
  IntentPanel,
  PlanPanel,
  ReportPanel,
  ReviewPanel,
  ToolCallsTable,
  VerificationPanel,
} from "@/components/workflows/outputs";
import { Timeline } from "@/components/workflows/timeline";
import { WorkflowGraph, nodeStatesFromEvents } from "@/components/workflows/workflow-graph";

function Pad({ children }: { children: React.ReactNode }) {
  return <div className="p-4">{children}</div>;
}

export function ExecutionConsole({ id }: { id: string }) {
  const { data: wf, error, mutate } = useWorkflow(id);
  const { data: topology } = useGraph();
  const { events, state: streamState } = useEventStream(id);
  const active = wf ? !TERMINAL_STATUSES.includes(wf.status as WorkflowStatus) : true;
  const { data: calls } = useToolCalls(id, active);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<unknown>(null);

  const states = useMemo(() => nodeStatesFromEvents(events, wf?.status ?? "running"), [events, wf?.status]);

  if (error) return <ErrorState error={error} onRetry={() => mutate()} />;
  if (!wf) {
    return (
      <div className="space-y-3" role="status" aria-label="Loading workflow">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-[480px] w-full" />
      </div>
    );
  }

  const st = wf.state as WorkflowState;
  const approvals = wf.approvals ?? [];
  const pending = approvals.filter((a) => a.status === "pending");
  const decided = approvals.filter((a) => a.status !== "pending");
  const changed = st.changed_files ?? {};
  const diffs = (approvals.find((a) => a.action_type === "open_pull_request")?.proposed_changes as { diffs?: Record<string, string> } | undefined)?.diffs;

  const cancel = async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      await api.workflows.cancel(id);
      await mutate();
    } catch (err) {
      setCancelError(err);
    } finally {
      setCancelling(false);
    }
  };

  const outputTabs: TabItem[] = [
    st.report && { id: "report", label: "Report", content: <Pad><ReportPanel report={st.report} results={st.action_results} /></Pad> },
    st.intent && { id: "intent", label: "Intent", content: <Pad><IntentPanel intent={st.intent} /></Pad> },
    st.context && { id: "context", label: "Context", content: <Pad><ContextPanel context={st.context} /></Pad> },
    st.plan && { id: "plan", label: "Plan", count: st.plan.tasks.length, content: <Pad><PlanPanel plan={st.plan} /></Pad> },
    st.debug && { id: "debug", label: "Diagnosis", content: <Pad><DebugPanel debug={st.debug} /></Pad> },
    st.review && { id: "review", label: "Review", count: st.review.findings.length, content: <Pad><ReviewPanel review={st.review} /></Pad> },
    st.verification && { id: "verification", label: "Verification", content: <Pad><VerificationPanel verification={st.verification} /></Pad> },
    Object.keys(changed).length > 0 && {
      id: "changes",
      label: "Changes",
      count: Object.keys(changed).length,
      content: <Pad><div className="space-y-3">{diffs ? Object.entries(diffs).map(([p, d]) => <DiffView key={p} path={p} diff={d} />) : Object.keys(changed).map((p) => <Mono key={p}>{p}</Mono>)}</div></Pad>,
    },
    { id: "tools", label: "Tool calls", count: calls?.length, content: calls ? <ToolCallsTable calls={calls} /> : <LoadingRows /> },
  ].filter(Boolean) as TabItem[];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted">
            <Link href="/workflows" className="hover:text-text">Workflows</Link> / <span className="font-mono">{wf.id.slice(0, 8)}</span>
          </p>
          <h1 className="mt-1 text-lg font-semibold tracking-tight text-text">{wf.title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
            <StatusBadge status={wf.status} />
            {wf.intent && <Badge tone="accent">{humanize(wf.intent)}</Badge>}
            {wf.mode === "demo" ? <SimulatedBadge label="Demo execution" /> : <Badge tone="ok">Live execution</Badge>}
            <span>started {relativeTime(wf.started_at ?? wf.created_at)}</span>
            <span>· {wf.step_count} steps</span>
            {wf.duration_ms ? <span>· {duration(wf.duration_ms)}</span> : null}
            {wf.current_node && <span>· at <Mono>{wf.current_node}</Mono></span>}
            {(st.security_findings ?? 0) > 0 && <Badge tone="err">{st.security_findings} injection warning(s)</Badge>}
          </div>
        </div>
        {active && (
          <Button variant="danger" onClick={cancel} loading={cancelling}>
            <Ban className="size-3.5" aria-hidden /> Cancel
          </Button>
        )}
      </div>
      {cancelError !== null && <ErrorState error={cancelError} compact />}
      {wf.error && (
        <div role="alert" className="rounded-md border border-err/30 bg-err-soft px-3 py-2 text-[13px] text-err">{wf.error}</div>
      )}

      <Card>{topology ? <WorkflowGraph topology={topology} states={states} height={300} /> : <Skeleton className="h-[300px]" />}</Card>

      {pending.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-[13px] font-semibold text-warn">Waiting for your approval</h2>
          {pending.map((a) => (
            <ApprovalCard key={a.id} approval={a} showWorkflowLink={false} onDecided={() => mutate()} />
          ))}
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Card className="flex h-[640px] flex-col">
          <CardHeader title="Execution timeline" description={`${events.length} events`} />
          <Timeline events={events} state={streamState} />
        </Card>
        <Card className="min-h-[640px]">
          {outputTabs.length > 1 ? (
            <Tabs key={st.report ? "finished" : "running"} items={outputTabs} initial={st.report ? "report" : undefined} />
          ) : (
            <EmptyState title="Agents are working" description="Structured outputs appear here as each stage completes." />
          )}
        </Card>
      </div>

      {decided.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-[13px] font-semibold text-text">Approval history</h2>
          {decided.map((a) => <ApprovalCard key={a.id} approval={a} showWorkflowLink={false} />)}
        </div>
      )}

      <Card>
        <CardHeader title="Original request" />
        <pre className="p-4 font-sans text-[13px] whitespace-pre-wrap text-text">{wf.request}</pre>
      </Card>
    </div>
  );
}
