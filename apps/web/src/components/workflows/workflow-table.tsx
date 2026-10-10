"use client";

import Link from "next/link";
import type { WorkflowSummary } from "@/lib/api/types";
import { duration, humanize, relativeTime } from "@/lib/format";
import { Badge, EmptyState } from "@/components/ui/primitives";
import { SimulatedBadge, StatusBadge } from "@/components/ui/status";

export function WorkflowTable({ workflows, emptyHint }: { workflows: WorkflowSummary[]; emptyHint?: string }) {
  if (workflows.length === 0) {
    return <EmptyState title="No workflows yet" description={emptyHint ?? "Submit an engineering request to start one."} />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted">
            <th className="px-4 py-2 font-medium">Workflow</th>
            <th className="px-3 py-2 font-medium">Intent</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Stage</th>
            <th className="px-3 py-2 text-right font-medium">Duration</th>
            <th className="px-4 py-2 text-right font-medium">Created</th>
          </tr>
        </thead>
        <tbody>
          {workflows.map((w) => (
            <tr key={w.id} className="border-b border-border/60 last:border-0 hover:bg-hover/50">
              <td className="max-w-[360px] px-4 py-2">
                <Link href={`/workflows/${w.id}`} className="block truncate text-text hover:text-accent">
                  {w.title}
                </Link>
                <span className="font-mono text-[11px] text-faint">{w.id.slice(0, 8)}</span>
              </td>
              <td className="px-3 py-2 text-muted">{w.intent ? humanize(w.intent) : "—"}</td>
              <td className="px-3 py-2">
                <div className="flex items-center gap-1.5">
                  <StatusBadge status={w.status} />
                  {(w.pending_approvals ?? 0) > 0 && <Badge tone="warn">{w.pending_approvals} to approve</Badge>}
                  {w.mode === "demo" && <SimulatedBadge label="Demo" />}
                </div>
              </td>
              <td className="px-3 py-2 font-mono text-[12px] text-muted">{w.current_node ?? "—"}</td>
              <td className="px-3 py-2 text-right text-muted tabular-nums">{duration(w.duration_ms)}</td>
              <td className="px-4 py-2 text-right text-muted" title={w.created_at}>
                {relativeTime(w.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
