"""Repository Intelligence agent: targeted context retrieval."""

from __future__ import annotations

import itertools
from typing import Any

from orchestra.agents.base import Agent, AgentContext
from orchestra.memory.store import MemoryKind
from orchestra.retrieval import parse_stack_trace
from orchestra.schemas.agents import AgentName, RelevantFile, RepositoryContext


def _readme_summary(text: str | None) -> str:
    if not text:
        return ""
    paras = [p.strip() for p in text.split("\n\n") if p.strip() and not p.strip().startswith(("#", "```", "!["))]
    return paras[0].replace("\n", " ")[:400] if paras else ""


class RepositoryAgent(Agent):
    name = AgentName.REPOSITORY
    title = "Repository Intelligence"
    responsibility = (
        "Inspects repository structure, ranks relevant source files and snippets, analyses dependencies, "
        "locates likely implementation points, and retrieves related documentation and prior knowledge."
    )
    instructions = (
        "Using the repository facts and the ranked snippets, summarise what the repository does and how the "
        "code relevant to the request is organised. For each relevant file give a concrete reason. Describe "
        "code relationships you can see in the snippets (calls, imports, data flow). Only reference paths "
        "that appear in the provided file list."
    )

    async def investigate(
        self, ctx: AgentContext, request: str, terms: list[str], refs: dict[str, Any]
    ) -> tuple[RepositoryContext, list[dict[str, Any]]]:
        issue = refs.get("issue") or {}
        evidence = request + "\n" + (issue.get("body") or "")
        frames = parse_stack_trace(evidence)
        priority = [f.path for f in frames]

        tree = await ctx.tool(self.name, "repo.tree", {})
        snapshot = await ctx.runtime.reader.snapshot()
        search = await ctx.tool(
            self.name,
            "repo.search",
            {
                "terms": terms[:40] or ["main"],
                "priority_paths": priority[:20],
                "top_k": ctx.settings.retrieval_top_k,
            },
        )
        hits = search.hits
        for hit in hits:
            await ctx.screen(self.name, hit.text, f"{hit.path}:{hit.start_line}")
        memories = await ctx.tool(self.name, "memory.recall", {"query": evidence[:4000], "limit": 3})
        readme_path = next((d for d in snapshot.docs if d.lower() == "readme.md"), None)
        readme = await ctx.runtime.reader.read(readme_path) if readme_path else None

        await ctx.say(
            self.name,
            f"Ranked {len(hits)} snippets from {len(tree.files)} files"
            f"{'; using ' + str(len(memories.hits)) + ' prior memories' if memories.hits else ''}",
            payload={"top_files": sorted({h.path for h in hits}), "skipped": search.skipped},
        )

        snippets = [h.model_dump() for h in hits]
        known_paths = set(tree.files)

        def demo() -> RepositoryContext:
            files: dict[str, RelevantFile] = {}
            for h in hits:
                if h.path not in files:
                    in_trace = any(h.path.endswith(p) or p.endswith(h.path) for p in priority)
                    files[h.path] = RelevantFile(
                        path=h.path,
                        reason="Appears in the reported stack trace"
                        if in_trace
                        else f"Matches request terms (BM25 score {h.score:.2f})",
                        score=h.score,
                        start_line=h.start_line,
                        end_line=h.end_line,
                    )
            relationships = []
            for frame_a, frame_b in itertools.pairwise(frames):
                if frame_b.function:
                    relationships.append(f"{frame_a.path} calls {frame_b.function}() in {frame_b.path}")
            return RepositoryContext(
                summary=_readme_summary(readme) or f"{ctx.runtime.full_name} ({', '.join(snapshot.languages)})",
                languages=sorted(snapshot.languages, key=lambda k: -snapshot.languages[k]),
                dependencies=snapshot.dependencies,
                entry_points=snapshot.entry_points,
                relevant_files=list(files.values()),
                code_relationships=relationships,
                documentation=[d for d in snapshot.docs if d in known_paths][:10],
                risks=[f"Skipped {s}" for s in search.skipped][:5],
            )

        context = (
            ctx.context()
            .add("Engineering request", request, priority=100)
            .add(
                "Repository facts",
                (
                    f"Name: {ctx.runtime.full_name}\nLanguages: {snapshot.languages}\n"
                    f"Dependencies: {snapshot.dependencies}\nEntry points: {snapshot.entry_points}\n"
                    f"Docs: {snapshot.docs}\nTests: {snapshot.tests[:20]}\nFiles ({len(tree.files)}):\n"
                    + "\n".join(tree.files[:400])
                ),
                priority=90,
            )
        )
        if issue:
            context.add_untrusted(
                f"Issue #{issue.get('number')}",
                issue.get("body") or "",
                source=f"issue #{issue.get('number')}",
                priority=85,
            )
        if readme:
            context.add_untrusted("README", readme[:6000], source=readme_path or "README", priority=60)
        for h in hits:
            context.add_untrusted(
                f"Snippet {h.path}:{h.start_line}-{h.end_line} (score {h.score:.2f})",
                h.text,
                source=h.path,
                priority=70,
            )
        for m in memories.hits:
            context.add(f"Prior memory ({m.kind}, similarity {m.score:.2f})", m.content, priority=40)

        result = await self.reason(ctx, context=context, schema=RepositoryContext, demo=demo)
        # Deterministic facts override model text; hallucinated paths are discarded.
        result.languages = sorted(snapshot.languages, key=lambda k: -snapshot.languages[k])
        result.dependencies = snapshot.dependencies
        result.relevant_files = [f for f in result.relevant_files if f.path in known_paths]
        result.documentation = [d for d in result.documentation if d in known_paths]
        return result, snippets

    async def analyze_repository(self, ctx: AgentContext, request: str, context_out: RepositoryContext) -> None:
        """Persist a repository summary to long-term memory after analysis."""
        await ctx.runtime.memory.remember(
            MemoryKind.REPOSITORY_SUMMARY,
            f"{ctx.runtime.full_name}: {context_out.summary}\nEntry points: {', '.join(context_out.entry_points)}\n"
            f"Key files: {', '.join(f.path for f in context_out.relevant_files[:8])}",
            repository_id=ctx.runtime.repository_id,
            workflow_id=ctx.workflow_id,
            metadata={"request": request[:500]},
        )
