"use client";

import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useSWRConfig } from "swr";
import { useApprovals } from "@/lib/api/hooks";
import { ApprovalCard } from "@/components/approvals/approval-card";
import { EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/ui/primitives";

const VIEWS = [
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
  { id: "expired", label: "Expired" },
] as const;

export function ApprovalsView() {
  const [view, setView] = useState<(typeof VIEWS)[number]["id"]>("pending");
  const { data, error, isLoading, mutate } = useApprovals(view);
  const { mutate: globalMutate } = useSWRConfig();

  const onDecided = async () => {
    await mutate();
    await globalMutate((key) => Array.isArray(key) && (key[0] === "approvals" || key[0] === "workflows"));
  };

  return (
    <>
      <PageHeader
        title="Approval center"
        description="Every external write (branches, pull requests, issues, comments) waits here for a human decision. Decisions are recorded with who made them and why."
      />
      <div role="tablist" aria-label="Approval status" className="mb-4 flex gap-1 border-b border-border">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            role="tab"
            type="button"
            aria-selected={view === v.id}
            onClick={() => setView(v.id)}
            className={`-mb-px border-b-2 px-3 py-2 text-[13px] ${view === v.id ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}
          >
            {v.label}
            {view === v.id && data ? <span className="ml-1.5 text-faint">{data.total}</span> : null}
          </button>
        ))}
      </div>
      {error ? (
        <ErrorState error={error} onRetry={() => mutate()} />
      ) : isLoading ? (
        <LoadingRows rows={3} label="Loading approvals" />
      ) : data && data.items.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="size-6" />}
          title={view === "pending" ? "Nothing waiting for approval" : `No ${view} approvals`}
          description={view === "pending" ? "When agents propose a GitHub write, it appears here first." : undefined}
        />
      ) : (
        <div className="space-y-3">
          {data?.items.map((a) => <ApprovalCard key={a.id} approval={a} onDecided={onDecided} />)}
        </div>
      )}
    </>
  );
}
