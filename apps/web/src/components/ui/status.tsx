import clsx from "clsx";
import { FlaskConical } from "lucide-react";
import { Badge } from "./primitives";

const STATUS: Record<string, { label: string; tone: "neutral" | "info" | "warn" | "ok" | "err"; dot: string }> = {
  pending: { label: "Pending", tone: "neutral", dot: "bg-faint" },
  running: { label: "Running", tone: "info", dot: "bg-info pulse-dot" },
  waiting_for_approval: { label: "Waiting for approval", tone: "warn", dot: "bg-warn" },
  completed: { label: "Completed", tone: "ok", dot: "bg-ok" },
  failed: { label: "Failed", tone: "err", dot: "bg-err" },
  cancelled: { label: "Cancelled", tone: "neutral", dot: "bg-faint" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" as const, dot: "bg-faint" };
  return (
    <Badge tone={s.tone}>
      <span aria-hidden className={clsx("size-1.5 rounded-full", s.dot)} />
      {s.label}
    </Badge>
  );
}

export function StatusDot({ status, className }: { status: string; className?: string }) {
  const s = STATUS[status] ?? STATUS.pending;
  return <span role="img" aria-label={s.label} className={clsx("inline-block size-2 rounded-full", s.dot, className)} />;
}

export function RiskBadge({ level }: { level: string }) {
  const tone = level === "high" ? "err" : level === "medium" ? "warn" : "ok";
  return <Badge tone={tone}>{level} risk</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const tone =
    severity === "critical" || severity === "high" ? "err" : severity === "medium" ? "warn" : severity === "low" ? "info" : "neutral";
  return <Badge tone={tone}>{severity}</Badge>;
}

export function CheckBadge({ status }: { status: string }) {
  const tone = status === "passed" ? "ok" : status === "failed" ? "err" : "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

/** Marks anything produced by demo heuristics or the simulated GitHub host. */
export function SimulatedBadge({ label = "Simulated", title }: { label?: string; title?: string }) {
  return (
    <Badge tone="sim" title={title ?? "Produced in demo mode: deterministic heuristics and a simulated GitHub host, not a live AI execution."}>
      <FlaskConical className="size-3" aria-hidden />
      {label}
    </Badge>
  );
}
