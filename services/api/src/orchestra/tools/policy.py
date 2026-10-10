"""Which agent may use which tool.

This mapping is enforced in code by the ToolExecutor. Model output can never
widen it: agents do not choose tools by name from model text, and write tools
additionally require an approved Approval bound to the same workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestra.schemas.agents import AgentName

AGENT_TOOLS: dict[AgentName, frozenset[str]] = {
    AgentName.ORCHESTRATOR: frozenset({"github.get_issue", "github.get_pull", "memory.recall"}),
    AgentName.REPOSITORY: frozenset(
        {"repo.tree", "repo.search", "repo.read_file", "github.list_issues", "github.get_issue", "memory.recall"}
    ),
    AgentName.PLANNING: frozenset({"memory.recall"}),
    AgentName.DEBUGGING: frozenset({"repo.read_file", "repo.search", "memory.recall"}),
    AgentName.CODE_REVIEW: frozenset({"repo.read_file", "github.get_pull", "github.get_pull_files"}),
    AgentName.VERIFICATION: frozenset({"sandbox.run_tests", "github.ci_status", "repo.read_file"}),
    AgentName.AUTOMATION: frozenset(
        {
            "github.create_branch",
            "github.commit_files",
            "github.open_draft_pull",
            "github.create_issue",
            "github.comment",
        }
    ),
    AgentName.REPORTING: frozenset(),
}


@dataclass
class ToolPolicy:
    """Static allowlist plus runtime overrides from Settings."""

    disabled_tools: set[str] = field(default_factory=set)
    mcp_grants: dict[str, set[AgentName]] = field(default_factory=dict)  # tool -> agents

    def allowed(self, agent: AgentName, tool: str) -> tuple[bool, str]:
        if tool in self.disabled_tools:
            return False, f"tool {tool} is disabled in settings"
        if tool in AGENT_TOOLS.get(agent, frozenset()):
            return True, "allowlisted"
        if agent in self.mcp_grants.get(tool, set()):
            return True, "granted by MCP configuration"
        return False, f"agent {agent.value} is not permitted to use {tool}"
