# djobs

**Shared, local repository memory for any coding agent — without letting hooks guess who owns the work.**

`djobs` separates two concerns that coding tools often mix together:

1. **Observations are automatic and read-only.** Tool results, Git working-tree changes, compaction, and session boundaries can be recorded without creating or moving a task.
2. **Ownership is explicit.** A task is claimed, handed off, or completed only through `checkpoint()` and `handoff()` (or their advanced CLI/API equivalents).

The core is client-neutral. Codex, Claude Code, Gemini CLI, and Kimi Code have built-in thin adapters, while any future or custom agent can use the same normalized event command, MCP server, or Git sidecar.

> Gemini and Kimi are model families. Automatic integration depends on the **client hosting the model**. A Kimi model inside Kimi Code, Cursor, or a custom agent has different lifecycle capabilities, so djobs keeps adapters outside the core.

## Quick start

```powershell
pipx install djobs
djobs setup all
djobs doctor
```

`setup all` detects and configures the clients installed on the machine:

- Codex
- Claude Code
- Gemini CLI
- Kimi Code

Missing clients are skipped with a copyable/manual instruction. Existing MCP servers, permissions, settings, and unrelated hooks are preserved.

After restarting an already-running client, opening the same Git repository gives each supported adapter a compact, read-only view of:

- explicitly tracked unfinished work and current owners;
- failed and recently completed evidence;
- recent tool observations;
- actual Git working-tree changes, including changes made by an agent that had no djobs hook.

Nothing is claimed merely because a session started, a prompt was submitted, a tool ran, or a turn ended.

For project-local VS Code wiring, `djobs init` now installs the same compact MCP and passive guidance. It does **not** install the legacy command-rewriting hook or create automatic queue tasks.

## Universal architecture

```text
                    ┌─────────────────────────────┐
Codex adapter ─────▶│                             │
Claude adapter ────▶│ client-neutral event input  │
Gemini adapter ────▶│                             │
Kimi adapter ──────▶│   bounded observations      │
custom adapter ────▶│   + Git snapshots           │
filesystem sidecar ▶│                             │
                    └──────────────┬──────────────┘
                                   │
                           ~/.djobs/global.db
                                   │
                    ┌──────────────┴──────────────┐
                    │ compact MCP / CLI reads     │
                    │ explicit task ownership     │
                    └─────────────────────────────┘
```

The shared SQLite database is repository-scoped with deterministic workspace IDs. SQLite WAL mode, busy timeouts, atomic claims, leases, and bounded payloads allow multiple local clients to coexist.

## What happens automatically

Automatic adapters may:

- load compact state at session start without claiming it;
- record successful and failed tool outcomes;
- snapshot Git state and report changed paths;
- save a small marker before context compaction;
- record a real session end where the client exposes one;
- heartbeat a task that the same session **already claimed explicitly**.

Automatic adapters never:

- turn every user prompt into a task;
- claim the newest pending task at startup;
- release work at every model `Stop` event;
- infer that work is complete from natural-language output;
- overwrite another client's lease.

## Explicit ownership flow

```text
Codex opens repository A
  -> SessionStart reads tasks and observations only
  -> checkpoint("Implement parser", path="src/parser.py")
  -> Codex now owns that explicit task lease
  -> tool observations refresh only that existing lease
  -> handoff(task_id, "Parser complete; edge tests remain")

Gemini CLI opens repository A
  -> SessionStart reads the released task and Git observations
  -> checkpoint("Implement parser", path="src/parser.py")
  -> resumes the same task instead of creating a duplicate
```

If an agent disappears without a handoff, its lease eventually expires and normal recovery makes the work available again.

## Built-in client adapters

| Client | User configuration | Passive events |
|---|---|---|
| Codex | `~/.codex/hooks.json` | session start/end, tool result, pre-compact |
| Claude Code | `~/.claude/settings.json` | session start/end, tool success/failure, pre-compact |
| Gemini CLI | `~/.gemini/settings.json` | session start/end, after-tool, pre-compress |
| Kimi Code | `~/.kimi-code/config.toml` | session start/end, tool success/failure, pre-compact |

Kimi's user-level MCP entry is merged into `~/.kimi-code/mcp.json`. The other clients use their supported MCP registration commands. Only entries containing `djobs.hook_entrypoint` or the marked Kimi block are replaced or removed.

Lifecycle matchers follow each client's native rules. In particular, Gemini lifecycle hooks omit a matcher so startup, resume, clear, compression, and exit reasons are not accidentally filtered by a regex-looking exact value.

For Codex, review and trust newly installed commands through `/hooks` when prompted. djobs does not bypass host security approval.

### Configure one client

```powershell
djobs setup codex
djobs setup claude
djobs setup gemini
djobs setup kimi
```

### Repair or remove only djobs

```powershell
djobs repair all
djobs remove gemini
djobs remove kimi
```

Malformed JSON is never replaced automatically, even during repair.

## Any other agent or future client

The normalized event entrypoint accepts an arbitrary client identifier:

```bash
# The client sends its native hook JSON on stdin.
djobs agent-event session-start --client my-agent
djobs agent-event post --client my-agent
djobs agent-event post-failure --client my-agent
djobs agent-event pre-compact --client my-agent
djobs agent-event session-end --client my-agent
```

An adapter only maps native event names and payload fields to this command. It does not implement queue logic.

For a client with no hook mechanism at all, use the agent-independent Git sidecar:

```bash
djobs observe /path/to/repository --watch
```

The sidecar polls every five seconds by default and records real working-tree transitions. Its fingerprint includes tracked, staged, and untracked content, so a second edit is detected even when `git status` still shows the same `M` state. Diff and file contents are hashed for comparison and are not stored in the observation database.

It cannot know the model's private prompt or reliably attribute a change to a process, but the next agent still receives grounded file-change evidence instead of an invented task summary.

MCP itself cannot force an arbitrary client to call a tool at session start. Therefore djobs does not claim that every possible client gets automatic context injection with no adapter. The universal guarantees are the shared data format, Git observation fallback, MCP/CLI access, and explicit ownership semantics.

## Observation durability and privacy

Observations use their own schema and never masquerade as jobs. Fresh SQLite and PostgreSQL schemas declare the same logical observation tables, and an incremental SQLite migration is included for existing operators.

- Snapshot compare-and-record is one immediate transaction, so concurrent clients do not duplicate the same repository transition.
- Each repository keeps at most 1,000 recent observations by default instead of growing forever.
- Metadata remains valid JSON even when truncated.
- Common bearer tokens, API keys, passwords, authorization values, and URL passwords are redacted on a best-effort basis before tool summaries are stored.
- Observation text, metadata, and MCP output remain bounded. Secret redaction is defense in depth, not a substitute for keeping credentials out of commands and tool output.

## Compact MCP tools

The default server exposes four tools:

| Tool | Purpose |
|---|---|
| `sync_workspace()` | Read tasks plus recent observations for the current repository under a token budget. It never claims work. |
| `checkpoint(summary, path?, details?)` | Deliberately create/resume and atomically claim one unit of work. |
| `handoff(task_id, evidence, completed?)` | Explicitly release or complete owned work with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Backward-compatible revision recovery for integrations already storing IDs. |

Lower-level queue tools remain available through `djobs-mcp-full`.

## Repository detection

The workspace resolver uses:

1. MCP client roots;
2. cwd supplied by a client, adapter, or event;
3. the enclosing Git repository root;
4. the process startup directory.

Starting in `repo/src/feature` resolves to `repo`. Windows `\` and `/` spellings compare equally, drive letters are case-insensitive, and trailing separators are ignored. Different repositories remain isolated in the shared database.

## Local-first and fail-open behavior

- Default database: `~/.djobs/global.db`, overridden by `DJOBS_DB`.
- No cloud account, Redis, or remote service is required.
- Stored task text and observations are untrusted data, never executable instructions.
- Tool output, metadata, and injected context are bounded.
- Git observations store concise status summaries and content fingerprints, not full file contents.
- Hook, sidecar, or storage failure does not block the coding client.
- No prompt text is automatically persisted as a task.
- The legacy explicit `djobs hook ...` command remains for compatibility but is not installed by `setup` or `init`.

## Compatibility status

| Surface | Validation level |
|---|---|
| Repository resolution, shared SQLite, atomic claims, leases, isolation, token bounds | Automated Python tests. |
| Passive observation versus explicit ownership | Automated integration tests against a real SQLite database. |
| Arbitrary client identity and normalized event protocol | Automated tests. |
| Codex, Claude, Gemini, and Kimi configuration merge | Unit tests against their documented configuration shapes. |
| Content-aware Git snapshots, concurrent deduplication, metadata validity, retention, and redaction | Executed unit and isolated SQLite tests. |
| SQLite/PostgreSQL logical observation-schema parity | Automated schema tests; PostgreSQL runtime execution still requires an available server. |
| Real installed clients and native Windows/macOS/Linux behavior | Requires machine-level verification; not claimed by the isolated build environment. |
| Unsupported clients | MCP/CLI and Git sidecar work; automatic context injection requires a thin adapter because no universal lifecycle-hook standard exists. |

## Advanced use

### Explicit database

```powershell
$env:DJOBS_DB = "D:\state\team-djobs.db"
djobs-mcp
```

### Per-repository database

```powershell
djobs mcp --db .djobs/state.db
```

Do not commit the database.

### Full MCP surface

```powershell
djobs-mcp-full
# or
python -m djobs.delta_mcp
```

Existing `correlation_id`, `resume_delta`, queue, audit, receipt, dashboard, pause, and token-savings interfaces remain available.

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
