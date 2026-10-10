import clsx from "clsx";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { ApiError } from "@/lib/api/client";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  size = "md",
  className,
  loading,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "sm" | "md"; loading?: boolean }) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium whitespace-nowrap transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-[13px]",
        variant === "primary" && "border-transparent bg-accent text-[#0b0d10] hover:bg-accent-strong",
        variant === "secondary" && "border-border bg-panel-2 text-text hover:bg-hover",
        variant === "ghost" && "border-transparent text-muted hover:bg-hover hover:text-text",
        variant === "danger" && "border-err/40 bg-err-soft text-err hover:bg-err/20",
        className,
      )}
    >
      {loading && <RefreshCw className="size-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={clsx("rounded-lg border border-border bg-panel", className)} />;
}

export function CardHeader({ title, description, actions, className }: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex items-start justify-between gap-3 border-b border-border px-4 py-3", className)}>
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold text-text">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-text">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Badge({ tone = "neutral", className, children, title }: {
  tone?: "neutral" | "accent" | "ok" | "warn" | "err" | "info" | "sim";
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] leading-4 font-medium whitespace-nowrap",
        tone === "neutral" && "bg-panel-2 text-muted ring-1 ring-border ring-inset",
        tone === "accent" && "bg-accent-soft text-accent",
        tone === "ok" && "bg-ok-soft text-ok",
        tone === "warn" && "bg-warn-soft text-warn",
        tone === "err" && "bg-err-soft text-err",
        tone === "info" && "bg-info-soft text-info",
        tone === "sim" && "bg-sim-soft text-sim",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={clsx("animate-pulse rounded bg-panel-2", className)} />;
}

export function LoadingRows({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-2 p-4">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({ title, description, action, icon }: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <div className="mb-3 text-faint">{icon ?? <Inbox className="size-6" aria-hidden />}</div>
      <p className="text-[13px] font-medium text-text">{title}</p>
      {description && <p className="mt-1 max-w-sm text-xs text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div role="alert" className={clsx("flex items-start gap-3 rounded-md border border-err/30 bg-err-soft text-err", compact ? "p-2.5" : "m-4 p-3")}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1 text-[13px]">
        <p className="font-medium">{message}</p>
        {requestId && <p className="mt-0.5 font-mono text-[11px] opacity-80">request {requestId}</p>}
      </div>
      {onRetry && (
        <Button size="sm" variant="ghost" onClick={onRetry} className="text-err hover:text-err">
          Retry
        </Button>
      )}
    </div>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-[13px]">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 text-text">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={clsx("font-mono text-[12px] text-text", className)}>{children}</code>;
}
