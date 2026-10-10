"""Example MCP server: read-only engineering documentation lookup.

Run standalone with `python examples/mcp/docs_server.py` (stdio transport).
Orchestra discovers its tools when listed in the MCP config (see docs/mcp.md).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

DOCS = {
    "error-handling": "Library functions must not raise on valid empty inputs; return neutral values (0, []).",
    "money": "Round money with round_money() at presentation boundaries only; never round intermediate values.",
    "releases": "Releases are cut from main after CI passes. Agents never merge or push to main.",
}

server = MCPServer(name="orchestra-docs", version="0.1.0")


@server.tool()
def search_docs(query: str, limit: int = 3) -> list[dict[str, str]]:
    """Search engineering guidelines by keyword."""
    words = {w.lower() for w in query.split() if len(w) > 2}
    scored = sorted(
        ((sum(w in (k + " " + v).lower() for w in words), k, v) for k, v in DOCS.items()), reverse=True
    )
    return [{"topic": k, "text": v} for score, k, v in scored[:limit] if score > 0]


@server.tool()
def get_doc(topic: str) -> str:
    """Return one guideline document by topic."""
    return DOCS.get(topic, f"No document named {topic!r}")


if __name__ == "__main__":
    server.run()
