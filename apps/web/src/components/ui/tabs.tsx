"use client";

import clsx from "clsx";
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";

export type TabItem = { id: string; label: ReactNode; content: ReactNode; count?: number };

export function Tabs({ items, initial, className }: { items: TabItem[]; initial?: string; className?: string }) {
  const [active, setActive] = useState(initial ?? items[0]?.id);
  const base = useId();
  const current = items.find((t) => t.id === active) ?? items[0];

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = items[(index + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length];
    setActive(next.id);
    document.getElementById(`${base}-tab-${next.id}`)?.focus();
  };

  return (
    <div className={className}>
      <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-border px-2">
        {items.map((t, i) => (
          <button
            key={t.id}
            id={`${base}-tab-${t.id}`}
            role="tab"
            type="button"
            aria-selected={t.id === current?.id}
            aria-controls={`${base}-panel-${t.id}`}
            tabIndex={t.id === current?.id ? 0 : -1}
            onClick={() => setActive(t.id)}
            onKeyDown={(e) => onKey(e, i)}
            className={clsx(
              "-mb-px flex items-center gap-1.5 border-b-2 px-2.5 py-2 text-[13px] whitespace-nowrap transition-colors",
              t.id === current?.id ? "border-accent text-text" : "border-transparent text-muted hover:text-text",
            )}
          >
            {t.label}
            {t.count !== undefined && <span className="rounded bg-panel-2 px-1 text-[11px] text-muted">{t.count}</span>}
          </button>
        ))}
      </div>
      {current && (
        <div role="tabpanel" id={`${base}-panel-${current.id}`} aria-labelledby={`${base}-tab-${current.id}`}>
          {current.content}
        </div>
      )}
    </div>
  );
}
