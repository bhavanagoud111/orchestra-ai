"use client";

import { ShieldCheck, ShieldX } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";
import { api } from "@/lib/api/client";
import type { Approval } from "@/lib/api/types";
import { humanize, relativeTime } from "@/lib/format";
import { Badge, Button, Card, ErrorState } from "@/components/ui/primitives";
import { DiffView } from "@/components/ui/diff-view";
import { RiskBadge, SimulatedBadge } from "@/components/ui/status";

type Changes = {
  branch?: string;
  diffs?: Record<string, string>;
  pr_title?: string;
  pr_body?: string;
  title?: string;
  body?: string;
  number?: number;
  labels?: string[];
};

function ProposedChanges({ approval }: { approval: Approval }) {
  const c = approval.proposed_changes as Changes;
  if (approval.action_type === "open_pull_request") {
    return (
      <div className="space-y-3">
        <p className="text-xs text-muted">
          Draft pull request from <code className="font-mono text-text">{c.branch}</code> — <span className="text-text">{c.pr_title}</span>
        </p>
        {Object.entries(c.diffs ?? {}).map(([path, diff]) => (
          <DiffView key={path} path={path} diff={diff} />
        ))}
        {c.pr_body && (
          <details className="rounded-md border border-border">
            <summary className="cursor-pointer px-3 py-1.5 text-xs text-muted">Pull request description</summary>
            <pre className="max-h-72 overflow-auto px-3 pb-3 font-sans text-xs whitespace-pre-wrap text-muted">{c.pr_body}</pre>
          </details>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      {c.title && <p className="text-[13px] text-text">{c.title}</p>}
      {c.number && <p className="text-xs text-muted">On #{c.number}</p>}
      {c.labels && c.labels.length > 0 && <div className="flex gap-1">{c.labels.map((l) => <Badge key={l}>{l}</Badge>)}</div>}
      {c.body && <pre className="max-h-72 overflow-auto rounded-md border border-border bg-bg p-3 font-sans text-xs whitespace-pre-wrap text-muted">{c.body}</pre>}
    </div>
  );
}

export function ApprovalCard({ approval, onDecided, showWorkflowLink = true }: {
  approval: Approval;
  onDecided?: (a: Approval) => void;
  showWorkflowLink?: boolean;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const reasonId = useId();
  const pending = approval.status === "pending";

  const decide = async (kind: "approve" | "reject") => {
    if (kind === "reject" && reason.trim().length < 3) {
      setError(new Error("Give a reason (at least 3 characters) when rejecting"));
      return;
    }
    setBusy(kind);
    setError(null);
    try {
      const updated = kind === "approve" ? await api.approvals.approve(approval.id, reason.trim() || undefined) : await api.approvals.reject(approval.id, reason.trim());
      onDecided?.(updated);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className={pending ? "border-warn/40" : undefined} aria-label={`Approval: ${approval.title}`}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[13px] font-semibold text-text">{approval.title}</h3>
            <RiskBadge level={approval.risk_level} />
            <Badge>{humanize(approval.action_type)}</Badge>
            {approval.simulated && <SimulatedBadge title="Executes against the simulated demo GitHub host." />}
          </div>
          <p className="mt-0.5 text-xs text-muted">
            {approval.description} · requested {relativeTime(approval.created_at)}
            {showWorkflowLink && (
              <>
                {" · "}
                <Link className="text-accent hover:underline" href={`/workflows/${approval.workflow_id}`}>
                  view workflow
                </Link>
              </>
            )}
          </p>
        </div>
        {!pending && (
          <Badge tone={approval.status === "approved" ? "ok" : approval.status === "rejected" ? "err" : "neutral"}>
            {approval.status} {approval.decided_by ? `by ${approval.decided_by}` : ""}
          </Badge>
        )}
      </div>
      <div className="grid gap-4 p-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-3 text-[13px]">
          <div>
            <p className="mb-1 text-xs font-semibold text-muted">Why this risk level</p>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-text marker:text-faint">
              {approval.risk_reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold text-muted">Requested permissions</p>
            <div className="flex flex-wrap gap-1">
              {approval.requested_permissions.map((p) => <Badge key={p} tone="warn">{p}</Badge>)}
            </div>
          </div>
          {approval.decision_reason && (
            <div>
              <p className="mb-1 text-xs font-semibold text-muted">Decision note</p>
              <p className="text-xs text-text">{approval.decision_reason}</p>
            </div>
          )}
        </div>
        <ProposedChanges approval={approval} />
      </div>
      {pending && (
        <div className="space-y-2 border-t border-border px-4 py-3">
          <label htmlFor={reasonId} className="sr-only">Decision note</label>
          <input
            id={reasonId}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Decision note (required to reject)"
            className="h-8 w-full rounded-md border border-border bg-bg px-2.5 text-[13px] text-text placeholder:text-faint focus:border-accent focus:outline-none"
          />
          {error !== null && <ErrorState error={error} compact />}
          <div className="flex justify-end gap-2">
            <Button variant="danger" onClick={() => decide("reject")} loading={busy === "reject"} disabled={busy !== null}>
              <ShieldX className="size-3.5" aria-hidden /> Reject
            </Button>
            <Button variant="primary" onClick={() => decide("approve")} loading={busy === "approve"} disabled={busy !== null}>
              <ShieldCheck className="size-3.5" aria-hidden /> Approve
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
