# djobs

**Install once. Open the same repository in Codex or Claude Code. Continue where the other agent stopped without asking either model to run a handoff command.**

`djobs` is a local-first durable checkpoint layer for coding agents. One shared SQLite database stores compact repository-scoped work state and evidence. Deterministic host hooks perform the normal synchronization lifecycle, while expiring leases prevent two agents from silently taking the same resumable task.

No cloud account, Redis, daemon, per-repository initialization, correlation ID, workspace ID, or manually supplied database path is required for the normal workflow.

## Quick Start

```powershell
pipx install djobs
djobs setup all
djobs doctor
```

Then restart any already-running Codex or Claude Code session.

For Codex, open `/hooks` once, review the installed `djobs` commands, and trust them. Codex deliberately does not execute new non-managed command hooks until the user approves their exact definitions.

After that, neither agent needs to remember a djobs command:

```text
Codex session starts
  -> SessionStart automatically finds the Git repository
  -> unfinished work is synchronized and atomically claimed

You submit a coding request
  -> UserPromptSubmit automatically creates or resumes a durable task

Codex uses Bash or edits files
  -> PostToolUse automatically heartbeats the lease and stores bounded evidence

The Codex turn stops
  -> Stop automatically releases resumable work

Claude Code opens the same repository
  -> SessionStart automatically claims that released task and injects its state
```

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

Setup registers the `djobs` MCP server and safely merges lifecycle hooks into:

- Codex: `~/.codex/hooks.json`
- Claude Code: `~/.claude/settings.json`

Only handler definitions containing `djobs.hook_entrypoint` are replaced or removed. Existing MCP servers, settings, permissions, and unrelated hooks remain untouched. When a host CLI is unavailable or rejects automatic MCP setup, djobs prints a one-line registration command instead of guessing.

## Automatic lifecycle

The default setup installs five host events:

| Event | Automatic behavior |
|---|---|
| `SessionStart` | Resolve the current Git repository, recover expired leases, synchronize compact state, and claim the newest resumable task. |
| `UserPromptSubmit` | Create or resume one repository-scoped coding task for the session and heartbeat its lease. |
| `PreToolUse` | Ensure resumable work is claimed and refresh its lease immediately before Bash execution. |
| `PostToolUse` | Record bounded tool evidence and refresh the owned task lease after Bash or file edits. |
| `Stop` | Release owned work with bounded turn evidence so another agent can claim it immediately. |

The lifecycle code is invoked by the host, not by model judgment. MCP tools remain available for explicit refreshes, named checkpoints, authoritative completion, and backward compatibility.

## Manual tools for overrides and diagnostics

The compact coding MCP exposes four tools:

| Tool | Purpose |
|---|---|
| `sync_workspace()` | Explicitly refresh current repository state under a token budget. Normal startup sync is automatic. |
| `checkpoint(summary, path?, details?)` | Deliberately split work into a named checkpoint or claim a specific path beyond the automatic session task. |
| `handoff(task_id, evidence, completed?)` | Explicitly release or complete work with authoritative bounded evidence. |
| `resume_delta(correlation_id, ...)` | Backward-compatible revision recovery for existing integrations. |

The lower-level queue API remains available through `djobs-mcp-full` for advanced clients that explicitly need enqueue, claim, heartbeat, lease, audit, fleet, and health tools.

## Repository detection and isolation

The workspace resolver uses this order:

1. MCP client roots.
2. A cwd supplied by the client or hook event.
3. The enclosing Git repository root.
4. The server's startup directory.

Starting from `repo/src/feature` resolves to `repo`. Windows `\\` and `/` spellings compare equally, drive letters are case-insensitive, and trailing separators are ignored. New records use a deterministic repository ID, while reads also search compatible legacy path-based `correlation_id` values.

Different repositories remain isolated even though Codex and Claude Code share one local database.

## Concurrency and recovery

Each agent registration records its host type, session identity, repository, and last-seen time. Claims use an immediate SQLite transaction. A running task has an expiring lease and heartbeat. If an agent crashes or disappears, expired work is recovered and can be claimed by the next session.

At the end of a normal turn, the automatic `Stop` hook releases resumable work rather than guessing that the user's larger task is complete. Use `handoff(..., completed=True)` when completion should be recorded authoritatively.

## Local-first and fail-open behavior

- The default database is `~/.djobs/global.db`, or `DJOBS_DB` when explicitly set.
- SQLite uses WAL mode and a busy timeout for concurrent local clients.
- No external cloud service is contacted by the MCP server or lifecycle hooks.
- Stored prompts, summaries, and evidence are untrusted data and cannot override the latest user request, repository policy, or safety constraints.
- Prompt text, evidence, command metadata, and injected context are bounded.
- Empty synchronization produces a very small response.
- Hook and storage errors return success with no injected instruction, allowing the original coding task to continue.
- Existing project-level command-wrapper compatibility continues to preserve command stdout, stderr, and exit codes.

## Compatibility status

| Surface | Validation level |
|---|---|
| Repository resolver, shared SQLite, leases, isolation, and token bounds | Executed automated Python tests. |
| Codex-to-Claude automatic prompt/tool/stop/session handoff | Executed integration simulation using separate host sessions against one real SQLite database. |
| Codex user-hook merge and command generation | Executed unit tests against the documented `~/.codex/hooks.json` schema. A real Codex host was not available in the isolated build environment. |
| Claude Code user-hook merge and command generation | Executed unit tests against the documented `~/.claude/settings.json` schema. A real Claude Code host was not available in the isolated build environment. |
| Codex hook trust | Requires one user review in `/hooks`; djobs does not and should not bypass this security step. |
| Native Windows host execution | Commands include an explicit Codex `commandWindows` override; real native-host execution still requires machine-level verification. |
| Other MCP hosts | The MCP protocol remains compatible, but automatic lifecycle setup currently targets Codex and Claude Code. |

## Advanced and backward-compatible use

### Explicit database

```powershell
$env:DJOBS_DB = "D:\\state\\team-djobs.db"
djobs-mcp
```

### Per-repository database

Per-repository mode remains available for advanced users:

```powershell
djobs mcp --db .djobs/state.db
```

Do not commit the database. The normal zero-config setup uses the shared user database plus repository scoping.

### Full MCP surface

```powershell
djobs-mcp-full
# or
python -m djobs.delta_mcp
```

Existing calls to `resume_delta(correlation_id=...)`, `resume_session`, enqueue/complete/fail, claim/heartbeat/release, and the Python queue APIs remain supported on their advanced entry points.

### Existing VS Code workflow

`djobs init`, `djobs install-mcp`, the VS Code extension, project hooks, dashboard, receipt, audit, and token-savings commands remain available. `djobs setup all` is the simpler user-scoped Codex/Claude path and does not write files into every project.

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
