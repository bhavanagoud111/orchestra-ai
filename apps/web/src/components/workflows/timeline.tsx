"use client";

import clsx from "clsx";
import {
  AlertOctagon,
  ArrowRightLeft,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  GitBranch,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { WorkflowEvent } from "@/lib/api/types";
import { clockTime, duration, humanize } from "@/lib/format";
import type { StreamState } from "@/lib/use-event-stream";
import { Badge, EmptyState } from "@/components/ui/primitives";
import { SimulatedBadge } from "@/components/ui/status";

const ICONS: Record<string, { icon: LucideIcon; className: string }> = {
  status_changed: { icon: CircleDot, className: "text-muted" },
  stage_started: { icon: ChevronRight, className: "text-info" },
  stage_completed: { icon: CheckCircle2, className: "text-ok" },
  stage_failed: { icon: AlertOctagon, className: "text-err" },
  routing_decision: { icon: GitBranch, className: "text-accent" },
  agent_message: { icon: Bot, className: "text-text" },
  agent_output: { icon: Bot, className: "text-accent" },
  tool_call: { icon: Wrench, className: "text-muted" },
  tool_result: { icon: ArrowRightLeft, className: "text-muted" },
  approval_requested: { icon: ShieldAlert, className: "text-warn" },
  approval_decided: { icon: ShieldCheck, className: "text-ok" },
  security_warning: { icon: ShieldAlert, className: "text-err" },
  error: { icon: AlertOctagon, className: "text-err" },
  retry: { icon: RotateCcw, className: "text-warn" },
};

export const FILTERS: { id: string; label: string; match: (e: WorkflowEvent) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "agents", label: "Agents", match: (e) => ["agent_message", "agent_output", "routing_decision"].includes(e.type) },
  { id: "tools", label: "Tool calls", match: (e) => e.type === "tool_call" || e.type === "tool_result" },
  { id: "stages", label: "Stages", match: (e) => e.type.startsWith("stage_") || e.type === "status_changed" },
  { id: "approvals", label: "Approvals", match: (e) => e.type.startsWith("approval_") },
  { id: "problems", label: "Problems", match: (e) => ["error", "stage_failed", "security_warning", "retry"].includes(e.type) || (e.type === "tool_result" && /failed|denied|timeout/.test(e.message)) },
];

function EventRow({ event }: { event: WorkflowEvent }) {
  const [open, setOpen] = useState(false);
  const meta = ICONS[event.type] ?? ICONS.status_changed;
  const Icon = meta.icon;
  const hasPayload = event.payload && Object.keys(event.payload).length > 0;
  const problem = ["error", "stage_failed", "security_warning"].includes(event.type);
  return (
    <li className={clsx("group border-b border-border/50 last:border-0", problem && "bg-err-soft/40")}>
      <div className="flex items-start gap-2.5 px-3 py-1.5">
        <span className="w-16 shrink-0 pt-px font-mono text-[11px] text-faint tabular-nums">{clockTime(event.created_at)}</span>
        <Icon className={clsx("mt-0.5 size-3.5 shrink-0", meta.className)} aria-label={humanize(event.type)} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            {event.agent && <span className="text-[11px] font-medium text-muted">{humanize(event.agent)}</span>}
            <span className={clsx("text-[13px] break-words", problem ? "text-err" : "text-text")}>{event.message}</span>
            {event.duration_ms !== null && event.duration_ms !== undefined && (
              <span className="font-mono text-[11px] text-faint">{duration(event.duration_ms)}</span>
            )}
            {event.simulated && event.type === "agent_output" && <SimulatedBadge label="Demo reasoning" />}
            {event.type === "agent_output" && (event.payload as { reasoning?: string } | null)?.reasoning === "llm" && (
              <Badge tone="ok">{String((event.payload as { model?: string }).model ?? "LLM")}</Badge>
            )}
          </div>
          {hasPayload && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="mt-0.5 text-[11px] text-faint hover:text-accent"
            >
              {open ? "Hide details" : "Details"}
            </button>
          )}
          {open && hasPayload && (
            <pre className="mt-1 max-h-80 overflow-auto rounded border border-border bg-bg p-2 font-mono text-[11px] leading-4 text-muted">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          )}
        </div>
        <span className="shrink-0 font-mono text-[10px] text-faint opacity-0 group-hover:opacity-100">#{event.seq}</span>
      </div>
    </li>
  );
}

export function Timeline({ events, state }: { events: WorkflowEvent[]; state?: StreamState }) {
  const [filter, setFilter] = useState("all");
  const [follow, setFollow] = useState(true);
  const scroller = useRef<HTMLDivElement>(null);
  const visible = useMemo(() => events.filter((FILTERS.find((f) => f.id === filter) ?? FILTERS[0]).match), [events, filter]);

  useEffect(() => {
    if (follow && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [visible.length, follow]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b border-border px-3 py-2">
        {FILTERS.map((f) => {
          const count = f.id === "all" ? events.length : events.filter(f.match).length;
          return (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
              className={clsx(
                "rounded px-2 py-0.5 text-xs",
                filter === f.id ? "bg-hover text-text" : "text-muted hover:text-text",
                f.id === "problems" && count > 0 && filter !== f.id && "text-err",
              )}
            >
              {f.label} <span className="text-faint">{count}</span>
            </button>
          );
        })}
        {state && (
          <span className="ml-auto flex items-center gap-1.5 text-[11px] text-muted" aria-live="polite">
            <span className={clsx("size-1.5 rounded-full", state === "live" ? "bg-ok pulse-dot" : state === "error" ? "bg-err" : "bg-faint")} />
            {state === "live" ? "Live" : state === "polling" ? "Polling" : state === "ended" ? "Ended" : state === "error" ? "Disconnected" : "Connecting"}
          </span>
        )}
      </div>
      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
        }}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {visible.length === 0 ? (
          <EmptyState title={events.length ? "No events match this filter" : "Waiting for the first event…"} />
        ) : (
          <ol aria-label="Execution timeline">
            {visible.map((e) => (
              <EventRow key={e.seq} event={e} />
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
