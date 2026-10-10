"use client";

import { FileCode2, FlaskConical, FolderGit2, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
import { useSWRConfig } from "swr";
import useSWR from "swr";
import { api } from "@/lib/api/client";
import {
  useGitHubStatus,
  useRepositories,
  useRepository,
  useRepositoryCI,
  useRepositoryFile,
  useRepositoryIssues,
  useRepositoryPulls,
  useRepositoryTree,
  useWorkflows,
} from "@/lib/api/hooks";
import type { RepositoryContext } from "@/lib/api/types";
import { humanize, relativeTime } from "@/lib/format";
import { CodeBlock } from "@/components/ui/diff-view";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, KeyValue, LoadingRows, Mono, PageHeader, Skeleton } from "@/components/ui/primitives";
import { SimulatedBadge } from "@/components/ui/status";
import { Tabs } from "@/components/ui/tabs";
import { ContextPanel } from "@/components/workflows/outputs";
import { WorkflowTable } from "@/components/workflows/workflow-table";

function ConnectPanel() {
  const { mutate } = useSWRConfig();
  const { data: gh } = useGitHubStatus();
  const { data: ghRepos } = useSWR(gh?.connected ? "github-repos" : null, api.github.repos);
  const [fullName, setFullName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const connect = async (provider: "demo" | "github", name: string) => {
    setBusy(provider);
    setError(null);
    try {
      await api.repositories.connect({ provider, full_name: name });
      setFullName("");
      await mutate("repositories");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void connect("github", fullName.trim());
  };

  return (
    <Card>
      <CardHeader title="Connect a repository" description="Read access is used for analysis. Writes happen only through approved actions." />
      <div className="space-y-4 p-4">
        <div className="flex items-center justify-between gap-3 rounded-md border border-sim/30 bg-sim-soft px-3 py-2.5">
          <div className="text-[13px]">
            <p className="flex items-center gap-1.5 font-medium text-sim"><FlaskConical className="size-3.5" /> Demo repository</p>
            <p className="text-xs text-muted"><Mono>acme/invoicekit</Mono>, a small Python project with a real bug, issues and a PR. No credentials needed.</p>
          </div>
          <Button onClick={() => connect("demo", "acme/invoicekit")} loading={busy === "demo"}>Connect demo</Button>
        </div>
        <form onSubmit={submit} className="space-y-2">
          <label htmlFor="gh-repo" className="text-xs font-medium text-muted">GitHub repository</label>
          <div className="flex gap-2">
            <input
              id="gh-repo"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="owner/name"
              list="gh-repos"
              disabled={!gh?.connected}
              className="h-8 flex-1 rounded-md border border-border bg-bg px-2.5 font-mono text-[13px] text-text placeholder:text-faint focus:border-accent focus:outline-none disabled:opacity-50"
            />
            <datalist id="gh-repos">{ghRepos?.map((r) => <option key={r.full_name} value={r.full_name} />)}</datalist>
            <Button type="submit" variant="primary" disabled={!gh?.connected || !/^[\w.-]+\/[\w.-]+$/.test(fullName.trim())} loading={busy === "github"}>
              Connect
            </Button>
          </div>
          {!gh?.connected && (
            <p className="text-xs text-muted">
              GitHub is not connected. <Link href="/settings" className="text-accent hover:underline">Add a token or sign in with GitHub</Link> in Settings.
            </p>
          )}
          {gh?.connected && gh.user && <p className="text-xs text-muted">Signed in as <Mono>{gh.user.login}</Mono>{ghRepos ? ` · ${ghRepos.length} accessible repositories` : ""}</p>}
        </form>
        {error !== null && <ErrorState error={error} compact />}
      </div>
    </Card>
  );
}

export function RepositoriesView() {
  const { data, error, isLoading, mutate } = useRepositories();
  return (
    <>
      <PageHeader title="Repositories" description="Connected repositories, their analysis, and the issues and pull requests agents can work on." />
      <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
        <Card>
          <CardHeader title="Connected" description={data ? `${data.total} repositories` : undefined} />
          {error ? <ErrorState error={error} onRetry={() => mutate()} /> : isLoading ? <LoadingRows /> : data && data.items.length === 0 ? (
            <EmptyState icon={<FolderGit2 className="size-6" />} title="No repositories yet" description="Connect the demo repository to try a full workflow without any credentials." />
          ) : (
            <ul className="divide-y divide-border/60">
              {data?.items.map((r) => (
                <li key={r.id}>
                  <Link href={`/repositories/${r.id}`} className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-hover/50">
                    <FolderGit2 className="size-4 text-faint" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 text-[13px] text-text">{r.full_name} {r.simulated && <SimulatedBadge label="Demo" />}</p>
                      <p className="truncate text-xs text-muted">{r.description ?? "No description"}</p>
                    </div>
                    <Badge>{r.visibility}</Badge>
                    <span className="text-xs text-faint">{r.last_analyzed_at ? `analyzed ${relativeTime(r.last_analyzed_at)}` : "not analyzed"}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <ConnectPanel />
      </div>
    </>
  );
}

function FilesTab({ id }: { id: string }) {
  const { data: tree, error } = useRepositoryTree(id);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const { data: file, error: fileError, isLoading } = useRepositoryFile(id, selected);
  const files = useMemo(() => (tree?.files ?? []).filter((f) => f.toLowerCase().includes(filter.toLowerCase())), [tree, filter]);
  if (error) return <ErrorState error={error} />;
  return (
    <div className="grid min-h-[420px] md:grid-cols-[280px_1fr]">
      <div className="border-b border-border md:border-r md:border-b-0">
        <input
          aria-label="Filter files"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter files…"
          className="h-9 w-full border-b border-border bg-transparent px-3 text-[13px] text-text placeholder:text-faint focus:outline-none"
        />
        <ul className="max-h-[520px] overflow-y-auto py-1">
          {!tree && <LoadingRows rows={6} />}
          {files.map((f) => (
            <li key={f}>
              <button
                type="button"
                onClick={() => setSelected(f)}
                aria-current={selected === f ? "true" : undefined}
                className={`flex w-full items-center gap-1.5 px-3 py-1 text-left font-mono text-[12px] ${selected === f ? "bg-hover text-text" : "text-muted hover:text-text"}`}
              >
                <FileCode2 className="size-3.5 shrink-0" aria-hidden />
                <span className="truncate">{f}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="min-w-0 p-4">
        {!selected ? (
          <EmptyState title="Select a file" description="Files are read on demand. Credential files and very large files are never loaded." />
        ) : fileError ? (
          <ErrorState error={fileError} compact />
        ) : isLoading || !file ? (
          <Skeleton className="h-64" />
        ) : file.content === null ? (
          <EmptyState title="File not loaded" description={file.skipped_reason ?? undefined} />
        ) : (
          <>
            <p className="mb-2 font-mono text-xs text-muted">{file.path}</p>
            <CodeBlock code={file.content ?? ""} className="max-h-[560px]" />
          </>
        )}
      </div>
    </div>
  );
}

function IssuesTab({ id, onInvestigate }: { id: string; onInvestigate: (text: string) => void }) {
  const { data, error } = useRepositoryIssues(id);
  if (error) return <ErrorState error={error} />;
  if (!data) return <LoadingRows />;
  if (!data.length) return <EmptyState title="No open issues" />;
  return (
    <ul className="divide-y divide-border/60">
      {data.map((i) => (
        <li key={i.number} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-[13px]">
          <span className="font-mono text-xs text-faint">#{i.number}</span>
          <span className="min-w-0 flex-1 truncate text-text">{i.title}</span>
          {i.labels.map((l) => <Badge key={l}>{l}</Badge>)}
          <span className="text-xs text-faint">{i.author}</span>
          <Button size="sm" onClick={() => onInvestigate(`Investigate issue #${i.number}: ${i.title}`)}>
            <Play className="size-3" aria-hidden /> Investigate
          </Button>
        </li>
      ))}
    </ul>
  );
}

function PullsTab({ id, onReview }: { id: string; onReview: (text: string) => void }) {
  const { data, error } = useRepositoryPulls(id);
  if (error) return <ErrorState error={error} />;
  if (!data) return <LoadingRows />;
  if (!data.length) return <EmptyState title="No open pull requests" />;
  return (
    <ul className="divide-y divide-border/60">
      {data.map((p) => (
        <li key={p.number} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-[13px]">
          <span className="font-mono text-xs text-faint">#{p.number}</span>
          <span className="min-w-0 flex-1 truncate text-text">{p.title}</span>
          {p.draft && <Badge>draft</Badge>}
          <Mono className="text-[11px] text-muted">{p.head} → {p.base}</Mono>
          <Button size="sm" onClick={() => onReview(`Review PR #${p.number}: ${p.title}`)}>
            <Play className="size-3" aria-hidden /> Review
          </Button>
        </li>
      ))}
    </ul>
  );
}

export function RepositoryDetailView({ id }: { id: string }) {
  const router = useRouter();
  const { data: repo, error, mutate } = useRepository(id);
  const { data: tree } = useRepositoryTree(id);
  const { data: ci } = useRepositoryCI(id);
  const { data: workflows } = useWorkflows({ repository_id: id, limit: 10 });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);

  const start = async (fn: () => Promise<{ id: string }>) => {
    setBusy(true);
    setActionError(null);
    try {
      const wf = await fn();
      router.push(`/workflows/${wf.id}`);
    } catch (err) {
      setActionError(err);
      setBusy(false);
    }
  };
  const run = (request: string) =>
    start(() => api.workflows.create({ request, repository_id: id, idempotency_key: `web-${crypto.randomUUID()}` }));

  if (error) return <ErrorState error={error} onRetry={() => mutate()} />;
  if (!repo) return <Skeleton className="h-[480px]" />;
  const analysis = repo.analysis as (RepositoryContext & { workflow_id?: string }) | null;

  return (
    <>
      <PageHeader
        title={repo.full_name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            {repo.description ?? "No description"}
            {repo.simulated && <SimulatedBadge label="Demo repository" />}
            <Badge>{repo.visibility}</Badge>
            <span>default branch <Mono>{repo.default_branch}</Mono></span>
            {ci && <Badge tone={ci.state === "success" ? "ok" : ci.state === "failure" ? "err" : "neutral"}>CI {ci.state}</Badge>}
          </span>
        }
        actions={
          <Button variant="primary" loading={busy} onClick={() => start(() => api.repositories.analyze(id))}>
            Analyze repository
          </Button>
        }
      />
      {actionError !== null && <ErrorState error={actionError} compact />}
      <Card>
        <Tabs
          items={[
            {
              id: "analysis",
              label: "Analysis",
              content: (
                <div className="p-4">
                  {analysis ? (
                    <>
                      <p className="mb-4 text-xs text-muted">
                        Last analyzed {relativeTime(repo.last_analyzed_at)}
                        {analysis.workflow_id && <> · <Link className="text-accent hover:underline" href={`/workflows/${analysis.workflow_id}`}>view workflow</Link></>}
                      </p>
                      <ContextPanel context={analysis} />
                    </>
                  ) : tree ? (
                    <div className="space-y-4">
                      <p className="text-[13px] text-muted">No AI analysis yet. Repository facts gathered without a model:</p>
                      <KeyValue
                        items={[
                          ["Files", tree.files.length],
                          ["Languages", Object.entries(tree.languages).map(([k, v]) => `${k} (${v})`).join(", ") || "—"],
                          ["Dependencies", tree.dependencies.join(", ") || "—"],
                          ["Entry points", tree.entry_points.join(", ") || "—"],
                          ["Tests", `${tree.tests.length} files`],
                          ["Docs", tree.docs.join(", ") || "—"],
                        ]}
                      />
                    </div>
                  ) : (
                    <LoadingRows />
                  )}
                </div>
              ),
            },
            { id: "files", label: "Files", count: tree?.files.length, content: <FilesTab id={id} /> },
            { id: "issues", label: "Issues", content: <IssuesTab id={id} onInvestigate={run} /> },
            { id: "pulls", label: "Pull requests", content: <PullsTab id={id} onReview={run} /> },
            { id: "workflows", label: "Workflows", count: workflows?.total, content: <WorkflowTable workflows={workflows?.items ?? []} /> },
            {
              id: "ci",
              label: "CI",
              content: ci ? (
                ci.checks.length ? (
                  <ul className="divide-y divide-border/60">
                    {ci.checks.map((c) => (
                      <li key={c.name} className="flex items-center gap-3 px-4 py-2 text-[13px]">
                        <Badge tone={c.conclusion === "success" ? "ok" : c.conclusion ? "err" : "neutral"}>{c.conclusion ?? c.status}</Badge>
                        <span className="text-text">{c.name}</span>
                        <span className="text-xs text-faint">on {ci.ref}</span>
                      </li>
                    ))}
                  </ul>
                ) : <EmptyState title={`No CI checks on ${ci.ref}`} />
              ) : <LoadingRows />,
            },
          ]}
        />
      </Card>
      <p className="mt-3 text-xs text-faint">Provider: {humanize(repo.provider)} · connected {relativeTime(repo.connected_at)}</p>
    </>
  );
}
