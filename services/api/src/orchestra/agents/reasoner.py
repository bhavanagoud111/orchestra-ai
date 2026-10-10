"""LLM reasoning backend for live mode.

Agents never receive tool access from the model: the model returns a
Pydantic-validated structured object, and the agent's own code decides what
to do with it. This keeps authorization decisions out of model control.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from orchestra.observability import get_logger
from orchestra.security.injection import UNTRUSTED_CONTENT_POLICY

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

BASE_SYSTEM = (
    "You are one agent inside Orchestra AI, a multi-agent engineering operations platform. "
    "Other agents and deterministic code handle the steps before and after yours. "
    "Respond only with the requested structured output. Be specific and grounded in the provided "
    "evidence; say what you don't know rather than guessing, and keep confidence values calibrated. "
    "You cannot execute tools or take actions; anything with side effects is proposed and then "
    "reviewed by a human. " + UNTRUSTED_CONTENT_POLICY
)


class ReasonerError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    model: str
    duration_ms: float


class Reasoner(Protocol):
    model: str

    async def structured(self, *, agent: str, instructions: str, context: str, schema: type[T]) -> tuple[T, Usage]: ...


class AnthropicReasoner:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        effort: str = "high",
        max_tokens: int = 16000,
        timeout_s: float = 300.0,
        max_retries: int = 3,
        fallbacks: bool = True,
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=max_retries)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks

    async def structured(self, *, agent: str, instructions: str, context: str, schema: type[T]) -> tuple[T, Usage]:
        system = f"{BASE_SYSTEM}\n\n# Your role: {agent}\n{instructions}"
        started = time.perf_counter()
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": context}],
            "output_format": schema,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        response: Any
        try:
            if self.fallbacks:
                response = await self.client.beta.messages.parse(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
            else:
                response = await self.client.messages.parse(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise ReasonerError("Anthropic API key was rejected") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ReasonerError(f"Anthropic API permission denied: {exc.message}") from exc
        except anthropic.BadRequestError as exc:
            raise ReasonerError(f"Model request rejected: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise ReasonerError("Model rate limit reached", transient=True) from exc
        except anthropic.APIStatusError as exc:
            raise ReasonerError(f"Model API error {exc.status_code}", transient=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise ReasonerError("Could not reach the model API", transient=True) from exc

        if response.stop_reason == "refusal":
            raise ReasonerError("The model declined this request")
        if response.stop_reason == "max_tokens":
            raise ReasonerError("Model output was truncated (max_tokens reached)", transient=True)
        parsed = response.parsed_output
        if parsed is None:
            raise ReasonerError("Model returned no structured output", transient=True)
        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return parsed, usage
