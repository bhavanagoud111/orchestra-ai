"""Request and response models for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    error: str
    detail: Any = None
    request_id: str | None = None


# --- workflows -----------------------------------------------------------------------


class WorkflowCreate(BaseModel):
    request: str = Field(min_length=5, max_length=20_000, description="Engineering request in natural language")
    repository_id: str = Field(min_length=1, max_length=32)
    title: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(
        default=None, max_length=128, description="Repeat-safe key; resubmitting returns the existing workflow"
    )


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    intent: str | None
    status: str
    mode: str
    repository_id: str | None
    created_by: str
    current_node: str | None
    step_count: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None = None
    pending_approvals: int = 0


class WorkflowDetail(WorkflowSummary):
    request: str
    result: dict[str, Any] | None
    state: dict[str, Any] = Field(default_factory=dict, description="Structured agent outputs from the checkpoint")
    approvals: list[ApprovalOut] = Field(default_factory=list)


class EventOut(BaseModel):
    id: int
    workflow_id: str
    seq: int
    type: str
    node: str | None
    agent: str | None
    message: str
    payload: dict[str, Any] | None
    duration_ms: float | None
    simulated: bool
    created_at: str


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str | None
    agent: str
    tool: str
    source: str
    permission: str
    arguments: dict[str, Any]
    status: str
    result_summary: str | None
    error: str | None
    approval_id: str | None
    simulated: bool
    started_at: datetime
    duration_ms: float | None


# --- approvals -----------------------------------------------------------------------


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    action_type: str
    title: str
    description: str
    risk_level: str
    risk_reasons: list[str]
    requested_permissions: list[str]
    proposed_changes: dict[str, Any]
    status: str
    decided_by: str | None
    decision_reason: str | None
    simulated: bool
    created_at: datetime
    decided_at: datetime | None


class ApprovalDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class RejectDecision(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


# --- repositories --------------------------------------------------------------------


class RepositoryConnect(BaseModel):
    provider: Literal["github", "demo"] = "github"
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    full_name: str
    default_branch: str
    description: str | None
    visibility: str
    html_url: str | None
    analysis: dict[str, Any] | None
    connected_at: datetime
    last_analyzed_at: datetime | None
    simulated: bool = False


class FileContent(BaseModel):
    path: str
    content: str | None
    skipped_reason: str | None = None


# --- agents, tools, metrics ----------------------------------------------------------


class AgentOut(BaseModel):
    name: str
    title: str
    responsibility: str
    tools: list[str]
    status: Literal["active", "idle"]
    current_task: dict[str, Any] | None
    last_execution: dict[str, Any] | None
    tool_calls_24h: int
    tool_failures_24h: int
    recent_tools: list[dict[str, Any]]


class ToolOut(BaseModel):
    name: str
    description: str
    permission: str
    source: str
    input_schema: dict[str, Any]
    timeout_s: float | None
    enabled: bool
    allowed_agents: list[str]


class ToolsResponse(BaseModel):
    tools: list[ToolOut]
    unavailable: dict[str, str]
    mcp_servers: list[dict[str, Any]]


class Metrics(BaseModel):
    workflows_by_status: dict[str, int]
    workflows_by_intent: dict[str, int]
    active_workflows: int
    pending_approvals: int
    connected_repositories: int
    avg_duration_ms: float | None
    tool_calls: dict[str, int]
    approvals_by_status: dict[str, int]
    executions_last_7d: list[dict[str, Any]]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    mode: str
    checks: dict[str, str]


class Me(BaseModel):
    name: str
    role: str
    mode: str


WorkflowDetail.model_rebuild()
