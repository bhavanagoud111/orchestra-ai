"""Shared agent infrastructure."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel

from orchestra.agents.reasoner import Reasoner
from orchestra.config import RunMode, Settings
from orchestra.memory.context import ContextBuilder
from orchestra.schemas.agents import AgentName
from orchestra.security.injection import InjectionFinding, scan
from orchestra.tools.builtin import ToolRuntime
from orchestra.tools.executor import ToolExecutor
from orchestra.tools.policy import AGENT_TOOLS
from orchestra.workflows.events import EventRecorder, EventType

T = TypeVar("T", bound=BaseModel)


class AgentError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class AgentContext:
    workflow_id: str
    mode: RunMode
    reasoner: Reasoner | None
    executor: ToolExecutor
    runtime: ToolRuntime
    events: EventRecorder
    settings: Settings
    node: str = ""
    injection_findings: list[InjectionFinding] = field(default_factory=list)

    @property
    def simulated(self) -> bool:
        return self.mode is RunMode.DEMO

    async def tool(self, agent: AgentName, name: str, args: dict[str, Any], **kw: Any) -> Any:
        outcome = await self.executor.call(
            workflow_id=self.workflow_id,
            agent=agent,
            tool=name,
            args=args,
            runtime=self.runtime,
            simulated=self.simulated,
            node=self.node,
            **kw,
        )
        return outcome.value

    async def say(
        self,
        agent: AgentName,
        message: str,
        payload: dict[str, Any] | None = None,
        type: EventType = EventType.AGENT_MESSAGE,
    ) -> None:
        await self.events.emit(
            self.workflow_id,
            type,
            message,
            node=self.node,
            agent=agent.value,
            payload=payload,
            simulated=self.simulated,
        )

    async def screen(self, agent: AgentName, text: str, source: str) -> list[InjectionFinding]:
        findings = scan(text, source)
        if findings:
            self.injection_findings.extend(findings)
            await self.events.emit(
                self.workflow_id,
                EventType.SECURITY,
                f"Possible prompt injection in {source}: {', '.join(sorted({f.rule for f in findings}))}. "
                "Content is treated as data only.",
                node=self.node,
                agent=agent.value,
                payload={"findings": [f.__dict__ for f in findings]},
            )
        return findings

    def context(self) -> ContextBuilder:
        return ContextBuilder(budget_chars=self.settings.context_char_budget)


class Agent:
    name: ClassVar[AgentName]
    title: ClassVar[str]
    responsibility: ClassVar[str]
    instructions: ClassVar[str]

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "name": cls.name.value,
            "title": cls.title,
            "responsibility": cls.responsibility,
            "tools": sorted(AGENT_TOOLS.get(cls.name, frozenset())),
        }

    async def reason(self, ctx: AgentContext, *, context: ContextBuilder, schema: type[T], demo: Callable[[], T]) -> T:
        """Produce a structured output: deterministic heuristics in demo mode, the LLM in live mode."""
        if ctx.mode is RunMode.DEMO or ctx.reasoner is None:
            if ctx.mode is RunMode.LIVE:
                raise AgentError("Live mode requires a configured model provider (ORCHESTRA_ANTHROPIC_API_KEY)")
            output = schema.model_validate(demo().model_dump())
            await ctx.say(
                self.name,
                f"{self.title} produced {schema.__name__} (deterministic demo reasoning)",
                payload={"output": output.model_dump(mode="json"), "reasoning": "demo-heuristic"},
                type=EventType.AGENT_OUTPUT,
            )
            return output
        from orchestra.agents.reasoner import ReasonerError

        rendered = context.render()
        try:
            output, usage = await ctx.reasoner.structured(
                agent=self.title, instructions=self.instructions, context=rendered, schema=schema
            )
        except ReasonerError as exc:
            raise AgentError(f"{self.title}: {exc}", transient=exc.transient) from exc
        await ctx.say(
            self.name,
            f"{self.title} produced {schema.__name__}",
            payload={
                "output": output.model_dump(mode="json"),
                "reasoning": "llm",
                "model": usage.model,
                "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
                "llm_ms": round(usage.duration_ms),
                "context_dropped": context.dropped,
            },
            type=EventType.AGENT_OUTPUT,
        )
        return output
