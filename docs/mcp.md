# MCP tools

Orchestra can load tools from [Model Context Protocol](https://modelcontextprotocol.io) servers
over stdio. Point `ORCHESTRA_MCP_CONFIG_PATH` at a JSON file (see
[`examples/mcp/mcp.example.json`](../examples/mcp/mcp.example.json)):

```json
{
  "servers": [{
    "name": "docs",
    "command": "python",
    "args": ["examples/mcp/docs_server.py"],
    "env": {},
    "timeout_s": 15,
    "tools": {
      "search_docs": { "permission": "read", "agents": ["debugging"] }
    }
  }]
}
```

## What happens at startup

1. Each server is launched in its own task with **only** `PATH` plus its `env` block, so
   Orchestra's own secrets are not inherited.
2. `initialize` and `tools/list` run under `timeout_s`.
3. Each tool is registered as `mcp.<server>.<tool>` with the server's JSON Schema for inputs.
4. A server that fails to start is reported in `GET /api/health` and `GET /api/tools`
   (`unavailable`); the rest of the system keeps working.

## Permissions

| Declared in `tools`? | Registered permission | Usable by |
| --- | --- | --- |
| yes, `"permission": "read"` | read | the listed `agents` |
| yes, `"permission": "write"` | write | nobody (no approval type authorizes MCP writes yet) |
| no | write | nobody |

Undeclared tools are visible in Settings → Tool permissions but cannot run. Any tool can also be
disabled there.

## Calls

Every MCP call goes through the same executor as built-in tools: input validation against the
tool's schema, timeout, output validation (against `outputSchema` when the server publishes one),
a `ToolCall` record, timeline events, and a prompt-injection scan of the response.

The bundled example server (`examples/mcp/docs_server.py`) is used by the test suite.

Built-in agents currently call MCP tools only when code asks for them by name; there is no
model-driven tool selection. Exposing granted MCP tools to agents' reasoning context is on the
roadmap.
