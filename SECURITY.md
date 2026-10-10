# Security

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the maintainer, using the contact details
on the repository owner's GitHub profile, rather than in a public issue. Include steps to
reproduce and the affected commit.

## Threat model

Orchestra AI reads repositories, issues and pull requests that may be written by anyone, passes
some of that content to a language model, and can write to GitHub. The main risks are:

1. untrusted content steering the model into harmful actions (prompt injection);
2. unauthorized or unintended writes to GitHub;
3. leaking credentials through logs, events, memory, PR bodies or the browser;
4. running untrusted repository code.

## Controls

### Authorization is enforced in code, not by the model
- Agents produce structured outputs; they cannot call tools. Agent code calls tools through
  `ToolExecutor`, which enforces a static per-agent allowlist (`tools/policy.py`).
- Write tools additionally require an `Approval` that a human approved, that belongs to the same
  workflow, and whose action type authorizes that tool. Each write needs an idempotency key.
- API endpoints check roles server-side (`viewer` < `operator` < `approver` < `admin`).
  Approving requires `approver`; settings require `admin`.
- Approval decisions are compare-and-set: an approval can leave `pending` exactly once.

### Limited write scope
- The GitHub client exposes only: create branch (must start with `orchestra/`, never the default
  branch), commit to such a branch (non-fast-forward updates rejected), open a **draft** PR,
  create an issue, comment. There is no merge, force-push, delete, or settings operation.
- Proposed edits cannot touch CI/CD configuration (`.github/workflows/`, etc.) or credential
  files, cannot escape the repository, and cannot contain credential-like strings.

### Prompt-injection handling
- Repository files, issues, PRs, docs and MCP responses are wrapped in
  `<untrusted_document>` blocks (with look-alike delimiters defanged) under a system policy
  that treats them as data.
- A heuristic scanner flags instruction overrides, approval-bypass requests, credential
  exfiltration requests, fake delimiters and tool coercion. Findings are emitted as
  `security_warning` events and raise affected approvals to **high** risk.
- The scanner is a signal for humans, not the control. The controls are the two sections above.

### Secrets
- API tokens, the Anthropic key and the GitHub token live in the API server's environment or,
  if saved from Settings, encrypted at rest with `ORCHESTRA_SECRET_KEY` (Fernet). The API never
  returns secret values.
- The browser never receives tokens. The web app's server-side proxy attaches its token and
  rejects cross-origin `POST`/`PUT` requests.
- Logs, events, tool-call records, memory and generated PR/issue text pass through redaction
  (known secret patterns plus exact-match for configured secrets).
- Credential files (`.env`, `*.pem`, `credentials.json`, …) and files containing credential-like
  strings are never read into model context or memory.
- MCP servers are started with only `PATH` plus their configured environment.

### Code execution
- Tests run only when the sandbox is enabled, only inside `docker run` with `--network none`,
  `--read-only`, a read-only workspace mount, a 64 MB `noexec` tmpfs, `--cap-drop ALL`,
  `no-new-privileges`, uid 65534, CPU/memory/PID limits and a wall-clock timeout. Nothing from
  the host (credentials, home directory, Docker socket) is mounted. Repository code is never
  executed on the host.
- The provided `docker-compose.yml` keeps the sandbox off, because enabling it from inside a
  container would require mounting the host Docker socket.

### Other
- Input validation on every endpoint and tool; per-credential rate limiting; request IDs on
  every response; structured logs.

## Known limitations

- The web console authenticates to the API with **one** token, so every console user gets that
  token's role. Deploy the console behind your own SSO/reverse proxy and choose the least role
  that fits, or call the API directly with per-person tokens.
- There is no separation-of-duties check (the same token can create a workflow and approve it).
- Rate limiting and the event bus are per process.
- Prompt-injection detection is heuristic and will miss novel phrasing.
- `ORCHESTRA_SECRET_KEY` rotation is not automated; rotating it makes stored secrets unreadable
  until they are re-entered.
