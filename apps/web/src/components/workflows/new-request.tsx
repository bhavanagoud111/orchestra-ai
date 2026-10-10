"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { useSWRConfig } from "swr";
import { api } from "@/lib/api/client";
import { useRepositories } from "@/lib/api/hooks";
import { Button, Card, ErrorState } from "@/components/ui/primitives";

const EXAMPLES = [
  "Investigate issue #12: checkout returns 500 for empty carts",
  "Review PR #9 before we merge it",
  "Plan the work for issue #14",
  "Give me an architecture overview of this repository",
];

export function NewRequest({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const { data: repos, isLoading } = useRepositories();
  const [repositoryId, setRepositoryId] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const textId = useId();
  const repoId = useId();

  const selectedRepo = repositoryId || repos?.items[0]?.id || "";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const wf = await api.workflows.create({
        request: text.trim(),
        repository_id: selectedRepo,
        // A key per submission makes double-clicks and retries safe.
        idempotency_key: `web-${crypto.randomUUID()}`,
      });
      await mutate((key) => Array.isArray(key) && key[0] === "workflows");
      router.push(`/workflows/${wf.id}`);
    } catch (err) {
      setError(err);
      setSubmitting(false);
    }
  };

  if (!isLoading && repos && repos.items.length === 0) {
    return (
      <Card className="p-4 text-[13px] text-muted">
        Connect a repository to submit engineering requests.{" "}
        <Link className="text-accent hover:underline" href="/repositories">
          Connect a repository →
        </Link>
      </Card>
    );
  }

  return (
    <Card>
      <form onSubmit={submit} className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label htmlFor={textId} className="text-[13px] font-semibold text-text">
            New engineering request
          </label>
          <div className="flex items-center gap-2">
            <label htmlFor={repoId} className="text-xs text-muted">
              Repository
            </label>
            <select
              id={repoId}
              value={selectedRepo}
              onChange={(e) => setRepositoryId(e.target.value)}
              className="h-7 rounded-md border border-border bg-panel-2 px-2 text-xs text-text"
            >
              {repos?.items.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.full_name}
                  {r.simulated ? " (demo)" : ""}
                </option>
              ))}
            </select>
          </div>
        </div>
        <textarea
          id={textId}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={compact ? 2 : 3}
          placeholder="Describe a bug, a feature to plan, a pull request to review, or paste a stack trace…"
          className="w-full resize-y rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text placeholder:text-faint focus:border-accent focus:outline-none"
        />
        {!compact && (
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => setText(ex)}
                className="rounded border border-border px-2 py-0.5 text-[11px] text-muted hover:border-border-strong hover:text-text"
              >
                {ex}
              </button>
            ))}
          </div>
        )}
        {error !== null && <ErrorState error={error} compact />}
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] text-faint">Agents propose changes; nothing is written to GitHub without your approval.</p>
          <Button type="submit" variant="primary" loading={submitting} disabled={text.trim().length < 5 || !selectedRepo}>
            Run workflow <ArrowRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      </form>
    </Card>
  );
}
