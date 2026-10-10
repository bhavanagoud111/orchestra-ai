# Architecture

This document describes how Orchestra AI is put together and why. For setup and usage,
see the [README](README.md).

## Components

```
 Browser ──► apps/web (Next.js 16)
               │  /api/orchestra/* route handler: attaches the API token server-side,
               │  rejects cross-origin writes, streams SSE through unchanged
               ▼
            services/api (FastAPI)
               ├─ api/            REST + SSE, RBAC, rate limiting, request IDs
               ├─ workflows/      LangGraph graph, runner, event recorder
               ├─ agents/         eight agents + the LLM reasoner
               ├─ tools/          tool registry, permission policy, executor
               ├─ integrations/   GitHub REST client, MCP client, Docker sandbox
               ├─ memory/         long-term memory store, context-window packing
               ├─ demo/           deterministic code host + fixture repository
               └─ db/             SQLAlchemy models (Alembic migrations)
                    │
                    ├─ SQLite (dev/tests) or Postgres (compose): workflows, events,
                    │   approvals, tool calls, memory, settings
                    └─ LangGraph checkpoints (SQLite file or Postgres)
```

`packages/shared-schemas/openapi.json` is the contract between the two apps. It is generated
from FastAPI and committed; the web app's TypeScript types are generated from it, and CI
fails if either is stale.

## The workflow graph

Every request runs through one LangGraph `StateGraph` (`workflows/graph.py`):

```
START → classify → retrieve_context → plan → dispatch
dispatch ─ bug_investigation  → debug → review → verify ─┐
         ├ code_review        → review ──────────────────┤
         ├ feature_planning   ───────────────────────────┤
         └ repository_analysis → report → END            │
verify ─(failed, revisions left)→ debug                  │
propose_actions ◄────────────────────────────────────────┘
propose_actions ─(no actions)→ report
propose_actions → approval_gate ⟂ interrupt() → execute_actions → report → END
```

| Node | Agent | What it does |
| --- | --- | --- |
| classify | Orchestrator | Parses issue/PR references, loads them, classifies intent, extracts search terms |
| retrieve_context | Repository Intelligence | Tree listing, BM25 chunk search, memory recall, README; builds `RepositoryContext` |
| plan | Planning | Produces a validated task DAG (`ExecutionPlan`) |
| dispatch | Orchestrator | Records the routing decision for the intent |
| debug | Debugging | Reads trace files, ranks hypotheses, proposes search/replace edits; edits are applied in memory |
| review | Code Review | Static rules + model review of the proposed diff or a PR's diff |
| verify | Verification | Patch/policy/syntax checks, review gate, sandboxed tests, CI baseline, acceptance criteria |
| propose_actions | Automation | Turns results into permission-scoped `ProposedAction`s (draft PR, issue, comment) |
| approval_gate | Orchestrator | Creates `Approval` rows (idempotently) with a risk assessment, then `interrupt()`s |
| execute_actions | Automation | Executes approved actions through write tools with idempotency keys |
| report | Reporting | Final report; writes long-term memories |

### State

`WorkflowState` (`workflows/state.py`) is a `TypedDict` holding only JSON-serialisable data;
agent outputs are stored as `model_dump(mode="json")`. That is what lets every step be
checkpointed and lets a paused workflow resume in a different process.

### Termination guarantees

- The only cycle is `verify → debug`, bounded by `max_debug_revisions` (default 1). When the
  limit is reached the verify node sets `blocked_reason` and routing proceeds forward.
- `recursion_limit` (`max_graph_steps`) caps node executions per run.
- Each node runs under `asyncio.wait_for(node_timeout_s)`; each running segment under
  `asyncio.timeout(workflow_timeout_s)`.
- Every path ends at `report → END`. Workflow status is always one of
  `pending`, `running`, `waiting_for_approval`, `completed`, `failed`, `cancelled`.

### Retries and failures

Nodes have a LangGraph `RetryPolicy` (3 attempts, exponential backoff with jitter) that retries
only *transient* errors: model rate limits/5xx/network, tool timeouts, GitHub 429/5xx. Other
exceptions fail the stage; the runner records the error, builds a deterministic report from the
last checkpoint, and marks the workflow `failed`. The GitHub client and the Anthropic SDK
also retry at the HTTP level.

### Human-in-the-loop

`approval_gate` calls `langgraph.types.interrupt()`. The runner sees pending `next` nodes in the
checkpoint and sets `waiting_for_approval`; no task or memory is held while waiting. When the
last pending approval is decided, the runner resumes the thread with
`Command(resume=decisions)`. Approval decisions use a compare-and-set update, so two approvers
cannot both decide the same approval.

### Restart behaviour

On startup, `WorkflowRunner.recover()` resubmits workflows left `running` (continuing from their
last checkpoint) and `pending`. A re-executed node may repeat read-only work; external writes
cannot be duplicated because each write carries an idempotency key and the executor returns the
recorded result of an earlier successful call with the same key. The GitHub client also checks
for an existing branch/PR before creating one.

## Agents and reasoning

Each agent (`agents/*.py`) gathers its inputs through tools, then produces a Pydantic-validated
structured output via `Agent.reason()`:

- **Live mode**: `AnthropicReasoner` calls `messages.parse` with the output schema, adaptive
  thinking and a configured effort level. Refusals, truncation, and API errors map to typed
  `ReasonerError`s (transient or not).
- **Demo mode**: a deterministic heuristic produces the same schema. These heuristics do real
  work on real data (BM25 retrieval, stack-trace parsing, a division-by-zero guard detector,
  static review rules, real patch application) but they are not AI. Every such output is
  emitted with `simulated: true` and labelled "Demo reasoning" in the UI.

Models never call tools or decide authorization. Deterministic code overrides model claims
where facts are known: repository languages/dependencies come from the tree, hallucinated file
paths are dropped, static security findings cannot be dismissed by the reviewer model, and
model-judged acceptance checks are always marked required.

## Tools and permissions

`tools/registry.py` defines `ToolSpec` (input/output models or JSON Schema, permission, timeout,
source). `tools/executor.py` enforces, in order:

1. the tool exists (unknown tools are denied and recorded);
2. the agent is allowed to use it (`tools/policy.py` static allowlist, plus MCP grants) and the
   tool is not disabled in Settings;
3. for `write` tools: an `Approval` in `approved` state, belonging to the same workflow, whose
   action type authorises this tool, plus an idempotency key;
4. input validation, timeout, output validation;
5. a `ToolCall` row and `tool_call`/`tool_result` events with redacted arguments.

Responses from non-built-in tools are scanned for prompt-injection patterns.

## MCP

`integrations/mcp/manager.py` launches each configured stdio server in its own owner task
(keeping the transport's cancel scope in one task), discovers tools, and registers them as
`mcp.<server>.<tool>`. Tools not declared in config default to `write` with no agent grants,
and no built-in approval type authorises an MCP write. Servers receive only `PATH` plus
their configured environment. See [docs/mcp.md](docs/mcp.md).

## Retrieval and memory

- **Targeted retrieval** (`retrieval.py`): rank paths by query terms and stack-trace references,
  fetch at most 25 candidate files (size-capped, credential files skipped), split into
  40-line chunks with 10-line overlap, rank chunks with BM25, return the top-k non-overlapping
  snippets.
- **Context packing** (`memory/context.py`): sections are packed by priority into a character
  budget; lowest priority is truncated first and recorded in the event payload.
- **Long-term memory** (`memory/store.py`): repository summaries, error investigations, approved
  decisions, failure patterns and outcomes, with retention (`expires_at`) and a per-repository
  cap. Content that looks like a credential is redacted; entries that are mostly secrets are
  not stored. The default embedder is deterministic feature hashing (no external service); it
  captures lexical overlap, not deep semantics. Ranking is in-process cosine similarity after
  SQL pre-filtering. At per-repository scale (hundreds of rows) that is fast; pgvector would
  become worthwhile with a semantic embedding model and much larger memories.

## Security model

See [SECURITY.md](SECURITY.md) for the full list. In short: untrusted content is fenced as data
and screened; models cannot invoke tools; writes require recorded approvals; write scope is
limited to `orchestra/*` branches, draft PRs, issues and comments; CI config and credential
files cannot be edited; tests run in a no-network, read-only, capability-dropped container;
tokens live only in the API server (and the web server's proxy) and are redacted from logs,
events and memory.

## Why these choices

- **LangGraph without LangChain.** LangGraph supplies state, checkpointing, interrupts and retry
  policies. Model access is a single structured-output call per agent, which the official
  Anthropic SDK does directly; LangChain would add an abstraction layer without removing code.
- **One Python service.** Agents, workflows and integrations share the database, executor and
  event recorder. Splitting them into separately versioned packages would add release overhead
  without a second consumer.
- **No Redis.** Execution, the event bus and rate limiting are in-process. This is the main
  scaling limit: running several API replicas would need a shared queue, pub/sub for events
  and a shared rate limiter (Redis would be the natural fit), plus a lease on workflow execution.
- **SSE, not WebSockets.** Event flow is one-directional; SSE resumes with `Last-Event-ID` and
  works through the same-origin proxy.
