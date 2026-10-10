"""Planning agent: converts a request into a validated task graph."""

from __future__ import annotations

from typing import Any

from orchestra.agents.base import Agent, AgentContext
from orchestra.schemas.agents import (
    AgentName,
    ExecutionPlan,
    Intent,
    IntentClassification,
    PlannedTask,
    RepositoryContext,
)


def _t(
    id: str,
    title: str,
    desc: str,
    agent: AgentName,
    deps: list[str],
    complexity: int,
    criteria: list[str],
    tools: list[str],
    perms: list[str],
) -> PlannedTask:
    return PlannedTask(
        id=id,
        title=title,
        description=desc,
        agent=agent,
        depends_on=deps,
        complexity=complexity,
        acceptance_criteria=criteria,
        required_tools=tools,
        required_permissions=perms,
    )


def template_plan(
    intent: IntentClassification, repo: RepositoryContext, request: str, refs: dict[str, Any]
) -> ExecutionPlan:
    files = [f.path for f in repo.relevant_files[:4]] or ["(no files ranked)"]
    issue = refs.get("issue") or {}
    subject = issue.get("title") or request.strip().splitlines()[0][:120]
    if intent.intent is Intent.BUG_INVESTIGATION:
        tasks = [
            _t(
                "T1",
                "Locate failure point",
                f"Trace the failure through {', '.join(files)}.",
                AgentName.REPOSITORY,
                [],
                2,
                ["The failing function and line are identified"],
                ["repo.search", "repo.read_file"],
                ["repo:read"],
            ),
            _t(
                "T2",
                "Diagnose root cause",
                "Rank hypotheses against the stack trace and code.",
                AgentName.DEBUGGING,
                ["T1"],
                3,
                ["At least one hypothesis cites concrete code evidence"],
                ["repo.read_file"],
                ["repo:read"],
            ),
            _t(
                "T3",
                "Propose minimal fix",
                "Prepare a search/replace patch that removes the failure.",
                AgentName.DEBUGGING,
                ["T2"],
                2,
                [f"{subject} no longer reproduces", "Existing behavior for valid inputs is unchanged"],
                ["repo.read_file"],
                ["repo:read"],
            ),
            _t(
                "T4",
                "Review proposed change",
                "Check correctness, security and test coverage of the patch.",
                AgentName.CODE_REVIEW,
                ["T3"],
                2,
                ["No high or critical review findings remain"],
                ["repo.read_file"],
                ["repo:read"],
            ),
            _t(
                "T5",
                "Verify",
                "Apply the patch in isolation; run syntax, policy and test checks.",
                AgentName.VERIFICATION,
                ["T4"],
                2,
                ["Patch applies cleanly", "Required checks pass"],
                ["sandbox.run_tests"],
                ["sandbox:execute"],
            ),
            _t(
                "T6",
                "Prepare draft pull request",
                "After approval, push an orchestra/* branch and open a draft PR.",
                AgentName.AUTOMATION,
                ["T5"],
                1,
                ["Draft PR references the issue", "No direct push to default branch"],
                ["github.create_branch", "github.commit_files", "github.open_draft_pull"],
                ["github:write (approved)"],
            ),
        ]
        risks = ["Fix may mask an upstream data problem rather than resolve it"]
        questions = [] if issue else ["Is there a reproducible input or failing test for this bug?"]
    elif intent.intent is Intent.FEATURE_PLANNING:
        tasks = [
            _t(
                "T1",
                "Map affected modules",
                f"Identify integration points in {', '.join(files)}.",
                AgentName.REPOSITORY,
                [],
                2,
                ["Every module that must change is listed"],
                ["repo.search"],
                ["repo:read"],
            ),
            _t(
                "T2",
                "Define behavior and edge cases",
                f"Specify the expected behavior for: {subject}.",
                AgentName.PLANNING,
                ["T1"],
                2,
                ["Edge cases and invalid inputs are enumerated"],
                [],
                [],
            ),
            _t(
                "T3",
                "Implement core change",
                "Implement the change behind existing public interfaces.",
                AgentName.DEBUGGING,
                ["T2"],
                3,
                ["Public API remains backwards compatible"],
                ["repo.read_file"],
                ["repo:read"],
            ),
            _t(
                "T4",
                "Add tests",
                "Cover the new behavior and edge cases with unit tests.",
                AgentName.VERIFICATION,
                ["T3"],
                2,
                ["New tests fail before and pass after the change"],
                ["sandbox.run_tests"],
                ["sandbox:execute"],
            ),
            _t(
                "T5",
                "Review",
                "Review the implementation for correctness and maintainability.",
                AgentName.CODE_REVIEW,
                ["T3", "T4"],
                2,
                ["No high or critical findings"],
                [],
                ["repo:read"],
            ),
            _t(
                "T6",
                "Create tracking issue",
                "After approval, file a tracking issue containing this plan.",
                AgentName.AUTOMATION,
                ["T2"],
                1,
                ["Issue lists tasks and acceptance criteria"],
                ["github.create_issue"],
                ["github:write (approved)"],
            ),
        ]
        risks = ["Scope may grow once edge cases are enumerated"]
        questions = ["Who owns sign-off on the behavioral specification?"]
    elif intent.intent is Intent.CODE_REVIEW:
        tasks = [
            _t(
                "T1",
                "Load change set",
                "Read the pull request diff and touched files.",
                AgentName.CODE_REVIEW,
                [],
                1,
                ["All changed files are loaded"],
                ["github.get_pull_files"],
                ["repo:read"],
            ),
            _t(
                "T2",
                "Review correctness and security",
                "Evaluate the diff for defects, security and tests.",
                AgentName.CODE_REVIEW,
                ["T1"],
                3,
                ["Each finding cites a file and a concrete risk"],
                [],
                ["repo:read"],
            ),
            _t(
                "T3",
                "Post review summary",
                "After approval, comment the review on the pull request.",
                AgentName.AUTOMATION,
                ["T2"],
                1,
                ["Comment matches the reviewed findings"],
                ["github.comment"],
                ["github:write (approved)"],
            ),
        ]
        risks = ["Static review cannot confirm runtime behavior"]
        questions = [] if intent.pull_number else ["Which pull request should be reviewed?"]
    else:
        tasks = [
            _t(
                "T1",
                "Survey structure",
                "Summarize layout, languages, dependencies and entry points.",
                AgentName.REPOSITORY,
                [],
                1,
                ["Summary covers every top-level module"],
                ["repo.tree"],
                ["repo:read"],
            ),
            _t(
                "T2",
                "Record repository memory",
                "Store the summary for future workflows.",
                AgentName.REPORTING,
                ["T1"],
                1,
                ["Summary is retrievable by later investigations"],
                [],
                [],
            ),
        ]
        risks = []
        questions = []
    return ExecutionPlan(
        summary=f"{intent.intent.value.replace('_', ' ').capitalize()} plan for: {subject}",
        tasks=tasks,
        risks=risks,
        open_questions=questions,
    )


class PlanningAgent(Agent):
    name = AgentName.PLANNING
    title = "Planning"
    responsibility = (
        "Converts requests into executable tasks with dependencies, complexity estimates, acceptance criteria, "
        "and the tools and permissions each task needs."
    )
    instructions = (
        "Produce an execution plan as a dependency graph of 2-8 tasks. Assign each task to the most suitable "
        "agent (repository_intelligence, planning, debugging, code_review, verification, automation, reporting). "
        "Task ids are T1..Tn; depends_on may only reference earlier ids and must not form cycles. Acceptance "
        "criteria must be objectively checkable. Any task that writes to GitHub must list "
        "'github:write (approved)' as a permission and be assigned to automation."
    )

    async def plan(
        self,
        ctx: AgentContext,
        request: str,
        intent: IntentClassification,
        repo: RepositoryContext,
        refs: dict[str, Any],
    ) -> ExecutionPlan:
        context = (
            ctx.context()
            .add("Engineering request", request, priority=100)
            .add("Intent", intent.model_dump_json(), priority=95)
            .add("Repository context", repo.model_dump_json(), priority=80)
        )
        if refs.get("issue"):
            context.add_untrusted(
                "Referenced issue",
                (refs["issue"].get("body") or "")[:6000],
                source=f"issue #{refs['issue'].get('number')}",
                priority=70,
            )
        plan = await self.reason(
            ctx, context=context, schema=ExecutionPlan, demo=lambda: template_plan(intent, repo, request, refs)
        )
        await ctx.say(
            self.name,
            f"Plan has {len(plan.tasks)} tasks",
            payload={"tasks": [{"id": t.id, "agent": t.agent.value, "depends_on": t.depends_on} for t in plan.tasks]},
        )
        return plan
