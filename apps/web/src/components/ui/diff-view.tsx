import clsx from "clsx";

/** Renders a unified diff with line-level highlighting. */
export function DiffView({ path, diff }: { path: string; diff: string }) {
  const lines = diff.split("\n").filter((l, i, all) => !(i === all.length - 1 && l === ""));
  return (
    <figure className="overflow-hidden rounded-md border border-border">
      <figcaption className="border-b border-border bg-panel-2 px-3 py-1.5 font-mono text-[12px] text-muted">{path}</figcaption>
      <pre className="overflow-x-auto bg-bg py-1 font-mono text-[12px] leading-5">
        {lines.map((line, i) => (
          <div
            key={i}
            className={clsx(
              "px-3",
              line.startsWith("+") && !line.startsWith("+++") && "bg-ok-soft text-ok",
              line.startsWith("-") && !line.startsWith("---") && "bg-err-soft text-err",
              line.startsWith("@@") && "text-info",
              (line.startsWith("+++") || line.startsWith("---")) && "text-faint",
            )}
          >
            {line || " "}
          </div>
        ))}
      </pre>
    </figure>
  );
}

export function CodeBlock({ code, className }: { code: string; className?: string }) {
  return (
    <pre className={clsx("overflow-x-auto rounded-md border border-border bg-bg p-3 font-mono text-[12px] leading-5 text-text", className)}>
      {code}
    </pre>
  );
}
