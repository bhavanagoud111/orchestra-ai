"""Typed LangGraph workflow state.

State holds only JSON-serializable data (agent outputs are stored as
``model_dump(mode="json")`` dicts) so every step can be checkpointed and a
workflow paused for approval can be resumed after a restart.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class WorkflowState(TypedDict, total=False):
    workflow_id: str
    request: str

    intent: dict[str, Any] | None
    refs: dict[str, Any]
    context: dict[str, Any] | None
    snippets: list[dict[str, Any]]
    plan: dict[str, Any] | None
    route: list[str]

    debug: dict[str, Any] | None
    original_files: dict[str, str]
    changed_files: dict[str, str]
    patch_errors: list[str]
    policy_violations: list[str]
    review: dict[str, Any] | None
    verification: dict[str, Any] | None
    revisions: int

    proposed_actions: list[dict[str, Any]]
    approval_ids: list[str]
    approval_decisions: list[dict[str, Any]]
    action_results: list[dict[str, Any]]

    report: dict[str, Any] | None
    blocked_reason: str | None
    security_findings: int
    errors: Annotated[list[str], operator.add]
