"use client";

import clsx from "clsx";
import {
  Bot,
  FolderGit2,
  LayoutDashboard,
  Menu,
  Moon,
  Settings,
  ShieldCheck,
  Sun,
  Workflow,
  X,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useState, useSyncExternalStore, type ReactNode } from "react";
import { SWRConfig } from "swr";
import { useApprovals, useHealth, useMe } from "@/lib/api/hooks";
import { SimulatedBadge, StatusDot } from "@/components/ui/status";
import { Badge } from "@/components/ui/primitives";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/workflows", label: "Workflows", icon: Workflow },
  { href: "/repositories", label: "Repositories", icon: FolderGit2 },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar({ pathname, pendingApprovals, onNavigate }: {
  pathname: string | null;
  pendingApprovals: number;
  onNavigate?: () => void;
}) {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-0.5 px-2">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname !== null && isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
              active ? "bg-hover text-text" : "text-muted hover:bg-hover/60 hover:text-text",
            )}
          >
            <Icon className="size-4" aria-hidden />
            <span className="flex-1">{label}</span>
            {href === "/approvals" && pendingApprovals > 0 && (
              <span aria-label={`${pendingApprovals} pending approvals`} className="rounded bg-warn-soft px-1.5 text-[11px] font-medium text-warn">
                {pendingApprovals}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

const THEME_EVENT = "orchestra-theme-change";
const subscribeTheme = (cb: () => void) => {
  window.addEventListener(THEME_EVENT, cb);
  return () => window.removeEventListener(THEME_EVENT, cb);
};

function ThemeToggle() {
  const light = useSyncExternalStore(
    subscribeTheme,
    () => document.documentElement.dataset.theme === "light",
    () => false,
  );
  const toggle = () => {
    const next = !light;
    if (next) document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem("orchestra-theme", next ? "light" : "dark");
    } catch {
      /* storage unavailable */
    }
    window.dispatchEvent(new Event(THEME_EVENT));
  };
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={light ? "Switch to dark theme" : "Switch to light theme"}
      className="rounded-md p-1.5 text-muted hover:bg-hover hover:text-text"
    >
      {light ? <Moon className="size-4" /> : <Sun className="size-4" />}
    </button>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 px-4 py-4">
      <Image src="/logo.svg" alt="" width={22} height={22} priority />
      <span className="text-[14px] font-semibold tracking-tight text-text">Orchestra AI</span>
    </Link>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { data: health, error } = useHealth();
  const { data: me } = useMe();
  const status = error ? "failed" : health?.status === "ok" ? "completed" : health ? "waiting_for_approval" : "pending";
  const label = error ? "API unreachable" : health?.status === "ok" ? "All systems normal" : health ? "Degraded" : "Checking…";
  return (
    <header className="sticky top-0 z-20 flex h-12 items-center gap-3 border-b border-border bg-bg/90 px-4 backdrop-blur">
      <button type="button" onClick={onMenu} aria-label="Open navigation" className="rounded-md p-1.5 text-muted hover:bg-hover md:hidden">
        <Menu className="size-4" />
      </button>
      <div className="flex items-center gap-2 text-xs text-muted" title={health ? Object.entries(health.checks).map(([k, v]) => `${k}: ${v}`).join("\n") : undefined}>
        <StatusDot status={status} />
        <span>{label}</span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        {health && (health.mode === "demo" ? <SimulatedBadge label="Demo mode" /> : <Badge tone="ok">Live mode</Badge>)}
        {me && <Badge title="Role granted to this web deployment's API token">{me.role}</Badge>}
        <ThemeToggle />
      </div>
    </header>
  );
}

function DemoBanner() {
  const { data: health } = useHealth();
  if (health?.mode !== "demo") return null;
  return (
    <div className="border-b border-sim/20 bg-sim-soft px-4 py-1.5 text-center text-xs text-sim">
      Demo mode: agent reasoning uses deterministic heuristics and GitHub writes go to a simulated host. Nothing here is a live AI execution.
    </div>
  );
}

/** usePathname() is request data under Cache Components, so it lives behind Suspense. */
function CurrentSidebar(props: { pendingApprovals: number; onNavigate?: () => void }) {
  return <Sidebar pathname={usePathname()} {...props} />;
}

function NavigationLinks(props: { pendingApprovals: number; onNavigate?: () => void }) {
  return (
    <Suspense fallback={<Sidebar pathname={null} {...props} />}>
      <CurrentSidebar {...props} />
    </Suspense>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { data: pending } = useApprovals("pending");
  const count = pending?.total ?? 0;
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-border bg-panel md:flex">
        <Brand />
        <NavigationLinks pendingApprovals={count} />
        <p className="mt-auto px-4 py-4 text-[11px] leading-4 text-faint">From engineering intent to coordinated execution.</p>
      </aside>
      {open && (
        <div className="fixed inset-0 z-30 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button type="button" aria-label="Close navigation" className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="relative flex h-full w-64 flex-col border-r border-border bg-panel">
            <div className="flex items-center justify-between pr-2">
              <Brand />
              <button type="button" aria-label="Close navigation" onClick={() => setOpen(false)} className="rounded-md p-1.5 text-muted hover:bg-hover">
                <X className="size-4" />
              </button>
            </div>
            <NavigationLinks pendingApprovals={count} onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setOpen(true)} />
        <DemoBanner />
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-6">{children}</main>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ shouldRetryOnError: true, errorRetryCount: 3, dedupingInterval: 1000 }}>
      <Frame>{children}</Frame>
    </SWRConfig>
  );
}
