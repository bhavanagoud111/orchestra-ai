"use client";

import Link from "next/link";
import { useAgents, useMetrics, useRepositories, useWorkflows } from "@/lib/api/hooks";
import { duration, humanize, relativeTime } from "@/lib/format";
import { Card, CardHeader, EmptyState, ErrorState, LoadingRows, Mono, PageHeader, Skeleton } from "@/components/ui/primitives";
import { SimulatedBadge } from "@/components/ui/status";
import { NewRequest } from "@/components/workflows/new-request";
import { WorkflowTable } from "@/components/workflows/workflow-table";

type ToolActivity = { agent: string; tool: string; status: string; at: string; workflow_id: string; simulated: boolean };

function Stat({ label, value, href, tone }: { label: string; value: number | string | undefined; href?: string; tone?: string }) {
  const body = (
    <Card className="px-4 py-3 transition-colors hover:border-border-strong">
      <p className="text-xs text-muted">{label}</p>
      {value === undefined ? <Skeleton className="mt-1.5 h-6 w-12" /> : <p className={`mt-0.5 text-xl font-semibold tabular-nums ${tone ?? "text-text"}`}>{value}</p>}
    </Card>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

function ExecutionsChart({ days }: { days: { date: string; completed: number; failed: number; other: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.completed + d.failed + d.other));
  return (
    <div className="flex h-36 items-end gap-2 px-4 pt-4 pb-2" role="img" aria-label="Workflow executions over the last 7 days">
      {days.map((d) => {
        const total = d.completed + d.failed + d.other;
        return (
          <div key={d.date} className="flex flex-1 flex-col items-center gap-1" title={`${d.date}: ${d.completed} completed, ${d.failed} failed, ${d.other} other`}>
            <div className="flex w-full max-w-8 flex-col-reverse overflow-hidden rounded-sm" style={{ height: `${(total / max) * 100}px` }}>
              <div className="bg-ok/80" style={{ flex: d.completed }} />
              <div className="bg-err/80" style={{ flex: d.failed }} />
              <div className="bg-faint/60" style={{ flex: d.other }} />
            </div>
            <span className="text-[10px] text-faint">{new Date(`${d.date}T00:00:00`).toLocaleDateString(undefined, { weekday: "short" })}</span>
          </div>
        );
      })}
    </div>
  );
}

export function OverviewView() {
  const { data: metrics, error: metricsError, mutate } = useMetrics();
  const { data: workflows, error: wfError, isLoading } = useWorkflows({ limit: 8 });
  const { data: agents } = useAgents();
  const { data: repos } = useRepositories();
  const by = metrics?.workflows_by_status ?? {};
  const activity: ToolActivity[] = (agents ?? [])
    .flatMap((a) => (a.recent_tools as Omit<ToolActivity, "agent">[]).map((t) => ({ ...t, agent: a.title })))
    .sort((a, b) => String(b.at).localeCompare(String(a.at)))
    .slice(0, 8);

  return (
    <>
      <PageHeader title="Overview" description="Workflow activity, approvals waiting on you, and what the agents have been doing." />
      {metricsError && <ErrorState error={metricsError} onRetry={() => mutate()} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Active workflows" value={metrics?.active_workflows} href="/workflows" tone="text-info" />
        <Stat label="Waiting for approval" value={metrics ? by.waiting_for_approval ?? 0 : undefined} href="/approvals" tone="text-warn" />
        <Stat label="Completed" value={metrics ? by.completed ?? 0 : undefined} tone="text-ok" />
        <Stat label="Failed" value={metrics ? by.failed ?? 0 : undefined} tone={(by.failed ?? 0) > 0 ? "text-err" : undefined} />
        <Stat label="Pending approvals" value={metrics?.pending_approvals} href="/approvals" />
        <Stat label="Repositories" value={metrics?.connected_repositories} href="/repositories" />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <NewRequest />
          <Card>
            <CardHeader title="Recent workflows" actions={<Link href="/workflows" className="text-xs text-accent hover:underline">All workflows</Link>} />
            {wfError ? <ErrorState error={wfError} /> : isLoading ? <LoadingRows /> : <WorkflowTable workflows={workflows?.items ?? []} />}
          </Card>
        </div>
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Executions, last 7 days"
              description={metrics?.avg_duration_ms ? `Average duration ${duration(metrics.avg_duration_ms)}` : undefined}
            />
            {metrics ? <ExecutionsChart days={metrics.executions_last_7d as never} /> : <Skeleton className="m-4 h-28" />}
            <div className="flex gap-3 px-4 pb-3 text-[11px] text-muted">
              <span className="flex items-center gap-1"><span className="size-2 rounded-sm bg-ok/80" /> completed</span>
              <span className="flex items-center gap-1"><span className="size-2 rounded-sm bg-err/80" /> failed</span>
              <span className="flex items-center gap-1"><span className="size-2 rounded-sm bg-faint/60" /> other</span>
            </div>
          </Card>
          <Card>
            <CardHeader title="Recent agent activity" actions={<Link href="/agents" className="text-xs text-accent hover:underline">Agents</Link>} />
            {activity.length === 0 ? (
              <EmptyState title="No tool activity yet" />
            ) : (
              <ul className="divide-y divide-border/60">
                {activity.map((t, i) => (
                  <li key={i} className="flex items-center gap-2 px-4 py-2 text-[13px]">
                    <span className="w-24 shrink-0 truncate text-xs text-muted">{t.agent}</span>
                    <Mono className="truncate">{String(t.tool)}</Mono>
                    {Boolean(t.simulated) && <SimulatedBadge label="Demo" />}
                    <span className={`ml-auto shrink-0 text-[11px] whitespace-nowrap ${t.status === "succeeded" ? "text-faint" : "text-err"}`}>
                      {t.status === "succeeded" ? relativeTime(String(t.at)) : String(t.status)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card>
            <CardHeader title="Connected repositories" />
            <ul className="divide-y divide-border/60">
              {(repos?.items ?? []).map((r) => (
                <li key={r.id} className="flex items-center gap-2 px-4 py-2 text-[13px]">
                  <Link href={`/repositories/${r.id}`} className="truncate text-text hover:text-accent">{r.full_name}</Link>
                  {r.simulated && <SimulatedBadge label="Demo" />}
                  <span className="ml-auto text-[11px] text-faint">{r.last_analyzed_at ? `analyzed ${relativeTime(r.last_analyzed_at)}` : humanize(r.provider)}</span>
                </li>
              ))}
              {repos && repos.items.length === 0 && <EmptyState title="No repositories connected" action={<Link className="text-xs text-accent" href="/repositories">Connect one</Link>} />}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
