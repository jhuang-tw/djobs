# djobs

**Install once. Connect Codex and Claude Code. Open the same repository. Continue where the other agent stopped.**

`djobs` is a local-first durable checkpoint layer for coding agents. It keeps compact task state and evidence in one shared SQLite database, automatically scopes that state to the Git repository currently open in the MCP client, and uses expiring leases so two agents do not silently work on the same task.

No cloud account, Redis, daemon, repository initialization, correlation ID, workspace ID, or SQLite path is required for the normal workflow.

## Quick Start

```powershell
pipx install djobs
djobs setup all
djobs doctor
```

Then:

1. Open the same Git repository in Codex and Claude Code.
2. Let either agent create a checkpoint for long or risky work.
3. Close or switch agents.
4. The next agent calls `sync_workspace()` and receives the unfinished work, recent evidence, active owners, and a compact next step.

Setup is user-scoped and idempotent. You do **not** run `djobs init` in every repository.

### Set up one host

```powershell
djobs setup codex
djobs setup claude
```

### Repair or remove only djobs

```powershell
djobs repair all
djobs remove codex
djobs remove claude
```

The setup commands use each host's MCP CLI and operate only on the server named `djobs`. Existing MCP servers remain untouched. When a host CLI is unavailable or rejects automatic setup, djobs prints a one-line command you can copy directly.

## What the agents see

The default coding MCP intentionally exposes four tools:

| Tool | Purpose |
|---|---|
| `sync_workspace()` | Resolve the current repo and return unfinished, failed, recently completed, and currently owned work under a token budget. |
| `checkpoint(summary, path?, details?)` | Save or resume one unit of work and atomically claim it with an expiring lease. |
| `handoff(task_id, evidence, completed?)` | Release work to another agent or complete it with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Backward-compatible revision recovery for existing integrations. |

The lower-level queue API remains available through `djobs-mcp-full` for advanced clients that explicitly need enqueue, claim, heartbeat, lease, audit, fleet, and health tools.

## How repository detection works

The workspace resolver uses this order:

1. MCP client roots.
2. A cwd supplied by the MCP client or request.
3. The enclosing Git repository root.
4. The MCP server's startup directory.

Starting from `repo/src/feature` resolves to `repo`. Windows `\` and `/` spellings compare equally, drive letters are case-insensitive, and trailing separators are ignored. New records use a deterministic repository ID, while reads also search compatible legacy path-based `correlation_id` values.

State for different repositories is isolated even though the hosts share one local database.

## Cross-agent handoff

A normal handoff is:

```text
Codex opens repo A
  -> checkpoint("Implement parser", path="src/parser.py")
  -> task is leased to the Codex session
  -> handoff(task_id, "Parser complete; edge-case tests remain")

Claude Code opens repo A
  -> sync_workspace()
  -> sees the pending task and Codex evidence
  -> checkpoint("Implement parser", path="src/parser.py")
  -> resumes the same task instead of creating a duplicate
```

Opening repo B returns only repo B state.

Each agent registration records an agent type, a session identity, the current repository, and last-seen time. Claims are atomic. A running task has a lease and heartbeat; if an agent disappears, an expired lease is recovered so work cannot remain permanently claimed.

## Local-first and fail-open behavior

- The default database is `~/.djobs/global.db`, or `DJOBS_DB` when explicitly set.
- SQLite uses WAL mode and a busy timeout for concurrent local clients.
- No external cloud service is contacted by the MCP server.
- Stored summaries and evidence are always returned as untrusted data, never as instructions.
- Payloads, evidence, and MCP responses are bounded.
- `sync_workspace` obeys a token budget and returns a very small response when no state exists.
- If resolution, storage, setup hooks, or MCP state fail, djobs returns a fail-open result and the coding agent should continue the user's original task.
- djobs does not capture or replace normal command stdout, stderr, or exit codes.

## Compatibility status

| Surface | Validation level |
|---|---|
| Repository resolver, shared SQLite, leases, isolation, token bounds | Executed automated Python tests. |
| Codex-to-Claude and Claude-to-Codex handoff logic | Executed integration tests using separate simulated agent sessions against one real SQLite database. |
| Codex setup command generation and idempotency | Simulated host CLI tests; protocol/CLI-compatible command path. |
| Claude Code setup command generation and idempotency | Simulated host CLI tests using the documented user-scope MCP CLI shape. |
| Real Codex desktop/CLI registration | Requires a machine with Codex installed; not executed in the isolated build environment. |
| Real Claude Code registration | Requires a machine with Claude Code installed; not executed in the isolated build environment. |
| Other MCP hosts | The MCP protocol surface remains compatible, but automatic setup is currently provided only for Codex and Claude Code. |

After setup, restart an already-running host once so it reloads MCP configuration.

## Advanced and backward-compatible use

### Explicit database

```powershell
$env:DJOBS_DB = "D:\state\team-djobs.db"
djobs-mcp
```

### Per-repository database

Per-repository mode remains available for advanced users:

```powershell
djobs mcp --db .djobs/state.db
```

Do not commit the database. The normal zero-config setup uses the shared user database and repository scoping instead.

### Full MCP surface

```powershell
djobs-mcp-full
# or
python -m djobs.delta_mcp
```

Existing calls to `resume_delta(correlation_id=...)`, `resume_session`, enqueue/complete/fail, claim/heartbeat/release, and the Python queue APIs remain supported on their existing advanced entry points.

### Existing VS Code workflow

`djobs init`, `djobs install-mcp`, the VS Code extension, hooks, dashboard, receipt, audit, and token-savings commands remain available. `djobs setup all` is the simpler cross-agent path and does not write project files.

## Useful commands

```powershell
djobs doctor
djobs receipt --correlation-id <legacy-or-explicit-id>
djobs audit
djobs dashboard
djobs token-savings
djobs pause
djobs unpause
```

## Development

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
# activate the venv
python -m pip install -e ".[dev,pg]"

ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q

cd vscode-ext
npm ci
npx tsc -p ./ --noEmit
npm run compile
```

See `CONTRIBUTING.md` and `AGENTS.md` before changing public behavior.

## License

MIT
