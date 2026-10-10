"""Tool specifications and registry.

Built-in tools and tools discovered from MCP servers share one ``ToolSpec``
shape so the executor can apply the same validation, permission, timeout, and
tracing rules regardless of where a tool comes from.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import jsonschema
from pydantic import BaseModel, ValidationError


class Permission(StrEnum):
    READ = "read"
    EXECUTE = "execute"  # runs code, only inside the sandbox
    WRITE = "write"  # external side effects; always requires an approved Approval


class ToolValidationError(ValueError):
    pass


Handler = Callable[[Any, Any], Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    permission: Permission
    handler: Handler
    input_model: type[BaseModel] | None = None
    input_schema: dict[str, Any] | None = None
    output_model: type[BaseModel] | None = None
    timeout_s: float | None = None
    source: str = "builtin"
    # Action types an approval must carry for this write tool to run.
    approval_actions: frozenset[str] = field(default_factory=frozenset)

    def schema(self) -> dict[str, Any]:
        if self.input_model is not None:
            return self.input_model.model_json_schema()
        return self.input_schema or {"type": "object", "properties": {}}

    def validate_input(self, args: dict[str, Any]) -> Any:
        if self.input_model is not None:
            try:
                return self.input_model.model_validate(args)
            except ValidationError as exc:
                raise ToolValidationError(f"Invalid input for {self.name}: {exc.errors(include_url=False)}") from exc
        if self.input_schema is not None:
            try:
                jsonschema.validate(args, self.input_schema)
            except jsonschema.ValidationError as exc:
                raise ToolValidationError(f"Invalid input for {self.name}: {exc.message}") from exc
        return args

    def validate_output(self, value: Any) -> Any:
        if self.output_model is None:
            return value
        if isinstance(value, self.output_model):
            return value
        try:
            return self.output_model.model_validate(value)
        except ValidationError as exc:
            raise ToolValidationError(f"Tool {self.name} returned an invalid response") from exc

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "source": self.source,
            "input_schema": self.schema(),
            "timeout_s": self.timeout_s,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.unavailable: dict[str, str] = {}  # source -> reason

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool {spec.name} already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def remove_source(self, source: str) -> None:
        for name in [n for n, t in self._tools.items() if t.source == source]:
            del self._tools[name]
