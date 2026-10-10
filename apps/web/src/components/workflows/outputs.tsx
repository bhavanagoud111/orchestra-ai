"use client";

import { AlertTriangle, CheckCircle2, ExternalLink } from "lucide-react";
import type {
  ActionResult,
  DebugReport,
  ExecutionPlan,
  FinalReport,
  IntentClassification,
  RepositoryContext,
  ReviewReport,
  ToolCall,
  VerificationReport,
} from "@/lib/api/types";
import { duration, humanize, pct, relativeTime } from "@/lib/format";
import { Badge, EmptyState, KeyValue, Mono } from "@/components/ui/primitives";
import { CheckBadge, SeverityBadge, SimulatedBadge } from "@/components/ui/status";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</h3>
      {children}
    </section>
  );
}

function List({ items, empty = "None" }: { items: string[]; empty?: string }) {
  if (!items.length) return <p className="text-[13px] text-faint">{empty}</p>;
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-text marker:text-faint">
      {items.map((x, i) => (
        <li key={i}>{x}</li>
      ))}
    </ul>
  );
}

export function IntentPanel({ intent }: { intent: IntentClassification }) {
  return (
    <KeyValue
      items={[
        ["Intent", <Badge key="i" tone="accent">{humanize(intent.intent)}</Badge>],
        ["Confidence", pct(intent.confidence)],
        ["Rationale", intent.rationale],
        ["References", [intent.issue_number && `issue #${intent.issue_number}`, intent.pull_number && `PR #${intent.pull_number}`].filter(Boolean).join(", ") || "—"],
        ["Search terms", <span key="t" className="flex flex-wrap gap-1">{intent.search_terms.map((t) => <Mono key={t} className="rounded bg-panel-2 px-1">{t}</Mono>)}</span>],
      ]}
    />
  );
}

export function ContextPanel({ context }: { context: RepositoryContext }) {
  return (
    <div className="space-y-5">
      <p className="text-[13px] text-text">{context.summary}</p>
      <KeyValue
        items={[
          ["Languages", context.languages.join(", ") || "—"],
          ["Entry points", context.entry_points.map((p) => <Mono key={p} className="mr-2">{p}</Mono>)],
          ["Dependencies", context.dependencies.join(", ") || "—"],
          ["Docs", context.documentation.map((p) => <Mono key={p} className="mr-2">{p}</Mono>)],
        ]}
      />
      <Section title="Relevant files">
        <table className="w-full text-[13px]">
          <tbody>
            {context.relevant_files.map((f) => (
              <tr key={f.path} className="border-b border-border/50 last:border-0">
                <td className="py-1.5 pr-3 align-top">
                  <Mono>{f.path}</Mono>
                  {f.start_line ? <span className="ml-1 font-mono text-[11px] text-faint">:{f.start_line}-{f.end_line}</span> : null}
                </td>
                <td className="py-1.5 pr-3 text-muted">{f.reason}</td>
                <td className="py-1.5 text-right font-mono text-[11px] text-faint">{f.score.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      {context.code_relationships.length > 0 && (
        <Section title="Code relationships">
          <List items={context.code_relationships} />
        </Section>
      )}
      {context.risks.length > 0 && (
        <Section title="Retrieval notes">
          <List items={context.risks} />
        </Section>
      )}
    </div>
  );
}

export function PlanPanel({ plan }: { plan: ExecutionPlan }) {
  return (
    <div className="space-y-4">
      <p className="text-[13px] text-text">{plan.summary}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="py-1.5 pr-3 font-medium">Task</th>
              <th className="py-1.5 pr-3 font-medium">Agent</th>
              <th className="py-1.5 pr-3 font-medium">Depends on</th>
              <th className="py-1.5 pr-3 font-medium">Complexity</th>
              <th className="py-1.5 font-medium">Acceptance criteria / permissions</th>
            </tr>
          </thead>
          <tbody>
            {plan.tasks.map((t) => (
              <tr key={t.id} className="border-b border-border/50 align-top last:border-0">
                <td className="py-2 pr-3">
                  <span className="font-mono text-[11px] text-faint">{t.id}</span> <span className="text-text">{t.title}</span>
                  <p className="text-xs text-muted">{t.description}</p>
                </td>
                <td className="py-2 pr-3 text-muted">{humanize(t.agent)}</td>
                <td className="py-2 pr-3 font-mono text-[12px] text-muted">{t.depends_on.join(", ") || "—"}</td>
                <td className="py-2 pr-3">
                  <span className="flex gap-0.5" aria-label={`complexity ${t.complexity} of 5`}>
                    {Array.from({ length: 5 }, (_, i) => (
                      <span key={i} className={`h-1.5 w-3 rounded-sm ${i < t.complexity ? "bg-accent" : "bg-panel-2"}`} />
                    ))}
                  </span>
                </td>
                <td className="py-2">
                  <List items={t.acceptance_criteria} />
                  {t.required_permissions.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {t.required_permissions.map((p) => (
                        <Badge key={p} tone={p.includes("write") ? "warn" : "neutral"}>{p}</Badge>
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Risks"><List items={plan.risks} /></Section>
        <Section title="Open questions"><List items={plan.open_questions} /></Section>
      </div>
    </div>
  );
}

export function DebugPanel({ debug }: { debug: DebugReport }) {
  return (
    <div className="space-y-4">
      {debug.error_signature && <Mono className="block rounded bg-err-soft px-2 py-1 text-err">{debug.error_signature}</Mono>}
      <Section title="Hypotheses">
        <ol className="space-y-2">
          {debug.hypotheses.map((h, i) => (
            <li key={i} className="rounded-md border border-border p-3">
              <div className="flex items-start justify-between gap-3">
                <p className="text-[13px] text-text">{h.root_cause}</p>
                <Badge tone={h.confidence >= 0.7 ? "ok" : h.confidence >= 0.4 ? "warn" : "neutral"}>{pct(h.confidence)}</Badge>
              </div>
              {h.evidence.length > 0 && <div className="mt-1.5"><List items={h.evidence} /></div>}
            </li>
          ))}
        </ol>
      </Section>
      {debug.proposed_fix ? (
        <Section title="Proposed fix">
          <p className="text-[13px] text-text">{debug.proposed_fix.summary}</p>
          {debug.proposed_fix.edits.map((e, i) => (
            <p key={i} className="text-xs text-muted"><Mono>{e.path}</Mono> — {e.rationale}</p>
          ))}
          {debug.proposed_fix.tests_to_add.length > 0 && <List items={debug.proposed_fix.tests_to_add.map((t) => `Suggested test: ${t}`)} />}
        </Section>
      ) : (
        <p className="text-[13px] text-muted">No fix proposed.</p>
      )}
      {debug.needs_more_evidence.length > 0 && (
        <Section title="Evidence requested"><List items={debug.needs_more_evidence} /></Section>
      )}
    </div>
  );
}

export function ReviewPanel({ review }: { review: ReviewReport }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge tone={review.verdict === "approve" ? "ok" : review.verdict === "request_changes" ? "err" : "warn"}>{humanize(review.verdict)}</Badge>
        <p className="text-[13px] text-text">{review.summary}</p>
      </div>
      {review.findings.length === 0 ? (
        <p className="text-[13px] text-muted">No findings.</p>
      ) : (
        <ul className="divide-y divide-border/60 rounded-md border border-border">
          {review.findings.map((f, i) => (
            <li key={i} className="flex items-start gap-3 px-3 py-2">
              <SeverityBadge severity={f.severity} />
              <div className="min-w-0 text-[13px]">
                <p className="text-text">{f.message}</p>
                <p className="text-xs text-muted">
                  {humanize(f.category)}
                  {f.path && <> · <Mono>{f.path}</Mono></>}
                  {f.suggestion && <> · {f.suggestion}</>}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted">Test coverage: {review.test_coverage}</p>
    </div>
  );
}

export function VerificationPanel({ verification }: { verification: VerificationReport }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge tone={verification.outcome === "passed" ? "ok" : verification.outcome === "failed" ? "err" : "warn"}>
          {humanize(verification.outcome)}
        </Badge>
        {verification.outcome === "unverified" && <p className="text-xs text-muted">Static checks passed, but tests were not executed.</p>}
      </div>
      <ul className="divide-y divide-border/60 rounded-md border border-border">
        {verification.checks.map((c) => (
          <li key={c.name} className="px-3 py-2">
            <div className="flex items-center gap-2">
              <CheckBadge status={c.status} />
              <Mono>{c.name}</Mono>
              <span className="text-[11px] text-faint">{c.kind}{c.required ? " · required" : ""}</span>
            </div>
            {c.details && <pre className="mt-1 font-sans text-xs whitespace-pre-wrap text-muted">{c.details}</pre>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ReportPanel({ report, results }: { report: FinalReport; results?: ActionResult[] }) {
  const tone = report.outcome === "completed" ? "ok" : report.outcome === "partial" ? "warn" : "err";
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2">
        <Badge tone={tone}>{humanize(report.outcome)}</Badge>
        <p className="text-[13px] font-medium text-text">{report.headline}</p>
      </div>
      {report.summary && <p className="text-[13px] text-muted">{report.summary}</p>}
      {report.attention_required.length > 0 && (
        <div className="rounded-md border border-warn/30 bg-warn-soft p-3">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-warn"><AlertTriangle className="size-3.5" /> Needs attention</p>
          <List items={report.attention_required} />
        </div>
      )}
      {results && results.length > 0 && (
        <Section title="External actions">
          <ul className="space-y-1">
            {results.map((r, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 text-[13px]">
                <Badge tone={r.status === "succeeded" ? "ok" : r.status === "failed" ? "err" : "neutral"}>{r.status}</Badge>
                <span className="text-text">{r.title}</span>
                {r.url && (r.url.startsWith("http") ? (
                  <a className="inline-flex items-center gap-1 text-accent hover:underline" href={r.url} target="_blank" rel="noreferrer">
                    open <ExternalLink className="size-3" />
                  </a>
                ) : <Mono className="text-muted">{r.url}</Mono>)}
                {r.simulated && <SimulatedBadge />}
                {r.error && <span className="text-xs text-err">{r.error}</span>}
                {r.reason && <span className="text-xs text-muted">({r.reason})</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Completed"><List items={report.completed} /></Section>
        <Section title="Pending"><List items={report.pending} /></Section>
      </div>
      {report.decisions.length > 0 && (
        <Section title="Agent decisions">
          <ul className="space-y-1.5">
            {report.decisions.map((d, i) => (
              <li key={i} className="flex gap-2 text-[13px]">
                <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-faint" />
                <span>
                  <span className="text-muted">{humanize(d.agent)}:</span> <span className="text-text">{d.decision}</span>
                  {d.rationale && <span className="block text-xs text-muted">{d.rationale}</span>}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

export function ToolCallsTable({ calls }: { calls: ToolCall[] }) {
  if (!calls.length) return <EmptyState title="No tool calls yet" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted">
            <th className="px-4 py-2 font-medium">Tool</th>
            <th className="px-3 py-2 font-medium">Agent</th>
            <th className="px-3 py-2 font-medium">Permission</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Result</th>
            <th className="px-3 py-2 text-right font-medium">Duration</th>
            <th className="px-4 py-2 text-right font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.id} className="border-b border-border/50 align-top last:border-0">
              <td className="px-4 py-1.5">
                <Mono>{c.tool}</Mono>
                {c.source !== "builtin" && <Badge className="ml-1">{c.source}</Badge>}
              </td>
              <td className="px-3 py-1.5 text-muted">{humanize(c.agent)}</td>
              <td className="px-3 py-1.5">
                <Badge tone={c.permission === "write" ? "warn" : c.permission === "execute" ? "info" : "neutral"}>{c.permission}</Badge>
              </td>
              <td className="px-3 py-1.5">
                <Badge tone={c.status === "succeeded" ? "ok" : c.status === "running" ? "info" : "err"}>{c.status}</Badge>
                {c.simulated && c.permission === "write" && <SimulatedBadge label="Sim" />}
              </td>
              <td className="max-w-[380px] px-3 py-1.5 text-xs break-words text-muted">{c.error ?? c.result_summary ?? "—"}</td>
              <td className="px-3 py-1.5 text-right font-mono text-[12px] text-muted">{duration(c.duration_ms)}</td>
              <td className="px-4 py-1.5 text-right text-xs text-muted">{relativeTime(c.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
