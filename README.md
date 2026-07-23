# djobs

**Copilot-first local repository memory, with a client-neutral core for other coding agents.**

`djobs` keeps two concerns separate:

1. **Observations are automatic and read-only.** Tool results, Git working-tree changes, compaction, and session boundaries can be recorded without creating or moving a task.
2. **Ownership is explicit.** A task is claimed, handed off, or completed only through `checkpoint()` and `handoff()` or the advanced CLI/API equivalents.

GitHub Copilot is the default host integration because Copilot CLI and VS Code Agent can share one user-level adapter while Copilot handles model selection. Switching between GPT, Claude, Gemini, or another model inside Copilot does not require a model-specific djobs installation.

The core remains client-neutral. Codex, Claude Code, Gemini CLI, and Kimi Code are optional host adapters, while future or custom agents can use the normalized event command, MCP server, or Git sidecar.

> Models and agent hosts are different integration layers. A Kimi or Gemini model running inside Copilot uses the Copilot adapter; the same model running inside a different CLI needs that CLI's adapter.

## Quick start

```powershell
pipx install djobs
djobs setup
djobs doctor
```

With no target, `djobs setup` configures **GitHub Copilot only**:

- registers the compact djobs MCP server with Copilot CLI;
- limits the registered server to `sync_workspace`, `checkpoint`, `handoff`, and `resume_delta`;
- writes the passive adapter to `~/.copilot/hooks/djobs.json`;
- uses the same hook file for Copilot CLI and VS Code Agent;
- preserves unrelated MCP servers and hook files.

Restart an already-running Copilot client after setup. Opening the same Git repository then gives Copilot a compact, read-only view of:

- explicitly tracked unfinished work and current owners;
- failed and recently completed evidence;
- recent tool observations;
- actual Git working-tree changes, including changes made by an uninstrumented agent.

Nothing is claimed merely because a session started, a prompt was submitted, a tool ran, or a turn ended.

### Local Copilot versus cloud agent

The default setup is for **local Copilot CLI and VS Code Agent**. They can reach the local MCP process and `~/.djobs/global.db`.

GitHub's cloud coding agent runs in an isolated, temporary environment. It cannot read the SQLite database on your computer. Sharing state between local Copilot and the cloud agent therefore requires a separately configured remote MCP or Git-backed/remote persistence backend; djobs does not pretend that a local database crosses that boundary automatically.

## Copilot-first architecture

```text
GPT / Claude / Gemini / other model
                 │
                 ▼
       GitHub Copilot host
    CLI + VS Code Agent adapter
                 │
        hooks + compact MCP
                 │
                 ▼
       ~/.djobs/global.db
                 │
      observations + explicit tasks
```

The Copilot adapter uses the host's native versioned hook format:

```text
~/.copilot/hooks/djobs.json
```

It records these passive lifecycle events:

- session start;
- successful and failed tool results;
- pre-compaction state;
- real session end.

It deliberately does **not** install `UserPromptSubmit` or `Stop` automation, so ordinary prompts do not become tasks and a model turn ending does not release ownership.

## What happens automatically

Automatic adapters may:

- load compact state at session start without claiming it;
- record successful and failed tool outcomes;
- snapshot Git state and report changed paths;
- save a small marker before context compaction;
- record a real session end where the host exposes one;
- heartbeat a task that the same session already claimed explicitly.

Automatic adapters never:

- turn every user prompt into a task;
- claim the newest pending task at startup;
- release work at every model `Stop` event;
- infer completion from natural-language output;
- overwrite another client's lease.

## Explicit ownership flow

```text
Copilot opens repository A
  -> SessionStart reads tasks and observations only
  -> checkpoint("Implement parser", path="src/parser.py")
  -> this Copilot session now owns that explicit lease
  -> tool hooks record observations without creating more tasks
  -> handoff(task_id, "Parser complete; edge tests remain")

Another local agent opens repository A
  -> sync_workspace reads the released task and Git observations
  -> checkpoint("Implement parser", path="src/parser.py")
  -> resumes the same task instead of creating a duplicate
```

If an agent disappears without a handoff, its lease eventually expires and normal recovery makes the work available again.

## Optional host adapters

Use a specific target only when that host runs independently from Copilot:

```powershell
djobs setup copilot
djobs setup codex
djobs setup claude
djobs setup gemini
djobs setup kimi
```

Explicitly configure every detected host only when you really want separate integrations:

```powershell
djobs setup all
```

| Host | User configuration | Passive events |
|---|---|---|
| GitHub Copilot CLI + VS Code Agent | `~/.copilot/hooks/djobs.json` | session start/end, tool success/failure, pre-compact |
| Codex | `~/.codex/hooks.json` | session start/end, tool result, pre-compact |
| Claude Code | `~/.claude/settings.json` | session start/end, tool success/failure, pre-compact |
| Gemini CLI | `~/.gemini/settings.json` | session start/end, after-tool, pre-compress |
| Kimi Code | `~/.kimi-code/config.toml` | one-time prompt context, session end, tool success/failure, pre-compact |

Kimi's MCP entry is merged into `~/.kimi-code/mcp.json`. Other hosts use their supported MCP registration commands. Only djobs-managed entries are replaced or removed. Malformed settings are never overwritten automatically.

For Codex, review and trust newly installed local commands through `/hooks` when prompted.

### Repair or remove only djobs

```powershell
djobs repair
djobs remove
djobs repair all
djobs remove kimi
```

Without a target, repair and remove also default to Copilot.

## Any future or custom agent

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

For a client with no hook mechanism, use the agent-independent Git sidecar:

```bash
djobs observe /path/to/repository --watch
```

The sidecar records real working-tree transitions. Its fingerprint includes tracked, staged, and bounded untracked content, so a second edit is detected even when `git status` still shows the same `M` state. Contents are hashed for comparison and are not stored as observation text.

MCP itself cannot force every possible client to call a tool at session start. The universal guarantees are the shared data format, Git observation fallback, MCP/CLI access, and explicit ownership semantics.

## Compact MCP tools

The default server exposes four tools:

| Tool | Purpose |
|---|---|
| `sync_workspace()` | Read tasks plus recent observations for the current repository under a token budget. It never claims work. |
| `checkpoint(summary, path?, details?)` | Deliberately create/resume and atomically claim one unit of work. |
| `handoff(task_id, evidence, completed?)` | Explicitly release or complete owned work with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Backward-compatible revision recovery for integrations already storing IDs. |

Lower-level queue tools remain available through `djobs-mcp-full`.

## Observation durability and privacy

Observations use their own schema and never masquerade as jobs. Fresh SQLite and PostgreSQL schemas declare the same logical observation tables, and an incremental SQLite migration is included for existing operators.

- Snapshot compare-and-record is one immediate transaction, so concurrent clients do not duplicate the same repository transition.
- Each repository keeps at most 1,000 recent observations by default.
- Metadata remains valid JSON even when bounded.
- Common bearer tokens, API keys, passwords, authorization values, and URL passwords are redacted on a best-effort basis before tool summaries are stored.
- Stored task text and observations are untrusted data, never executable instructions.
- Hook, sidecar, or storage failure is fail-open and does not block coding.

## Repository detection

The resolver uses:

1. MCP client roots;
2. cwd supplied by a host, adapter, or event;
3. the enclosing Git repository root;
4. the process startup directory.

Starting in `repo/src/feature` resolves to `repo`. Windows, WSL, and common Git Bash spellings share one repository identity, while compatible aliases keep earlier path-based state readable.

## Local-first configuration

- Default database: `~/.djobs/global.db`, overridden by `DJOBS_DB`.
- No cloud account, Redis, or remote service is required for local clients.
- Git observations store concise status summaries and content fingerprints, not full file contents.
- No prompt text is automatically persisted as a task.
- The legacy explicit `djobs hook ...` command remains for compatibility but is not installed by `setup` or `init`.

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

## Compatibility status

| Surface | Validation level |
|---|---|
| Copilot hook document, idempotent install/remove, MCP registration shape, and Copilot-default setup | Automated unit tests. |
| Repository resolution, shared SQLite, atomic claims, leases, isolation, and token bounds | Automated Python tests. |
| Passive observation versus explicit ownership | Automated integration tests against SQLite. |
| Optional Codex, Claude, Gemini, and Kimi configuration merge | Unit tests against documented configuration shapes. |
| Content-aware Git snapshots, concurrent deduplication, metadata validity, retention, and redaction | Unit and isolated SQLite tests. |
| Real installed clients and native Windows/macOS/Linux behavior | Requires machine-level verification; not claimed by the isolated build environment. |
| Copilot cloud agent | Requires a remote or Git-backed persistence backend; local SQLite is intentionally not advertised as cloud-shared. |

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
