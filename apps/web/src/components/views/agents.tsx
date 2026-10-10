"use client";

import Link from "next/link";
import { useAgents } from "@/lib/api/hooks";
import type { Agent } from "@/lib/api/types";
import { duration, humanize, relativeTime } from "@/lib/format";
import { Badge, Card, ErrorState, Mono, PageHeader, Skeleton } from "@/components/ui/primitives";
import { SimulatedBadge } from "@/components/ui/status";

const WRITE_TOOLS = new Set(["github.create_branch", "github.commit_files", "github.open_draft_pull", "github.create_issue", "github.comment"]);

export function AgentCard({ agent }: { agent: Agent }) {
  const activeTask = agent.current_task as { workflow_id: string; title: string; node: string } | null;
  const last = agent.last_execution as { workflow_id: string; node: string; at: string; duration_ms: number | null; simulated: boolean } | null;
  return (
    <Card className="flex flex-col" aria-label={`${agent.title} agent`}>
      <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold text-text">{agent.title}</h2>
          <Mono className="text-[11px] text-faint">{agent.name}</Mono>
        </div>
        <Badge tone={agent.status === "active" ? "info" : "neutral"}>
          <span aria-hidden className={`size-1.5 rounded-full ${agent.status === "active" ? "bg-info pulse-dot" : "bg-faint"}`} />
          {agent.status === "active" ? "Active" : "Idle"}
        </Badge>
      </div>
      <div className="flex flex-1 flex-col gap-3 px-4 py-3 text-[13px]">
        <p className="text-muted">{agent.responsibility}</p>
        <div>
          <p className="text-xs text-faint">Current task</p>
          {activeTask ? (
            <Link href={`/workflows/${activeTask.workflow_id}`} className="text-accent hover:underline">
              {activeTask.title} <span className="text-faint">({activeTask.node})</span>
            </Link>
          ) : (
            <p className="text-muted">—</p>
          )}
        </div>
        <div>
          <p className="text-xs text-faint">Last execution</p>
          {last ? (
            <p className="flex flex-wrap items-center gap-1.5 text-text">
              <Link href={`/workflows/${last.workflow_id}`} className="hover:text-accent">{humanize(last.node)}</Link>
              <span className="text-muted">{relativeTime(last.at)} · {duration(last.duration_ms)}</span>
              {last.simulated && <SimulatedBadge label="Demo" />}
            </p>
          ) : (
            <p className="text-muted">Never run</p>
          )}
        </div>
        <div>
          <p className="mb-1 text-xs text-faint">Permitted tools</p>
          <div className="flex flex-wrap gap-1">
            {agent.tools.length ? agent.tools.map((t) => <Badge key={t} tone={WRITE_TOOLS.has(t) ? "warn" : "neutral"} title={WRITE_TOOLS.has(t) ? "Write tool: runs only after approval" : undefined}>{t}</Badge>) : <span className="text-xs text-muted">No tools (reasoning only)</span>}
          </div>
        </div>
        <div className="mt-auto border-t border-border pt-3">
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="text-faint">Tool activity (24h)</span>
            <span className="text-muted tabular-nums">
              {agent.tool_calls_24h} calls{agent.tool_failures_24h > 0 && <span className="text-err"> · {agent.tool_failures_24h} failed</span>}
            </span>
          </div>
          <ul className="space-y-0.5">
            {agent.recent_tools.slice(0, 3).map((t, i) => (
              <li key={i} className="flex items-center gap-2 text-xs">
                <span className={`size-1.5 rounded-full ${t.status === "succeeded" ? "bg-ok" : "bg-err"}`} aria-label={String(t.status)} />
                <Mono className="truncate text-[11px]">{String(t.tool)}</Mono>
                <span className="ml-auto text-faint">{relativeTime(String(t.at))}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

export function AgentsView() {
  const { data, error, mutate } = useAgents();
  return (
    <>
      <PageHeader
        title="Agents"
        description="Each agent has one responsibility and a fixed tool allowlist enforced by the server. Write tools additionally require an approved action."
      />
      {error && <ErrorState error={error} onRetry={() => mutate()} />}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {data ? data.map((a) => <AgentCard key={a.name} agent={a} />) : Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-80" />)}
      </div>
    </>
  );
}
