# djobs

**Local repository memory and handoff for coding agents.**

`djobs` is intentionally local-first:

- MCP servers run on the user's machine;
- hooks run on the user's machine;
- state is stored in local SQLite;
- Git observations are produced from the local working tree;
- no hosted service, remote database, cloud queue, or account is required.
- Python 3.10+ is supported.

GitHub Copilot CLI and VS Code Agent are the default integration host because one local Copilot adapter can serve every model selected inside Copilot. GPT, Claude, Gemini, or another model running inside Copilot all use the same djobs MCP and hooks.

The core remains client-neutral. Codex, Claude Code, Gemini CLI, Kimi Code, and custom agents may use optional local adapters, the same MCP server, or the Git sidecar.

## Quick start

```powershell
pipx install djobs
djobs setup
djobs doctor
```

With no target, `djobs setup` configures **local GitHub Copilot only**:

- registers the compact djobs MCP with Copilot CLI;
- exposes only `sync_workspace`, `checkpoint`, `handoff`, and `resume_delta`;
- writes passive local hooks to `~/.copilot/hooks/djobs.json`;
- lets Copilot CLI and VS Code Agent share that hook file;
- preserves unrelated MCP servers and hook files.

Restart an already-running Copilot client after setup. Opening the same local Git repository then gives Copilot a compact view of:

- unfinished work and current owners;
- failed and recently completed evidence;
- recent tool observations;
- actual local Git working-tree changes.

Nothing is claimed merely because a session started, a prompt was submitted, a tool ran, or a turn ended.

### VS Code extension

The headless VS Code extension follows the same model. **djobs: Set up / Repair djobs**:

- installs or upgrades the local Python package;
- registers the four-tool MCP server through VS Code's native provider;
- installs the passive Copilot lifecycle adapter;
- does not install the legacy smart command-checkpoint hook;
- does not add a task sidebar, polling loop, or cloud service.

To upgrade a command-line installation later:

```powershell
pipx upgrade djobs
djobs repair
```

## Local Copilot-first architecture

```text
GPT / Claude / Gemini / other model
                 │
                 ▼
       local GitHub Copilot host
      CLI + VS Code Agent adapter
                 │
          hooks + compact MCP
                 │
                 ▼
        ~/.djobs/global.db
                 │
      observations + explicit tasks
```

The default design stops at the local machine. It does not create a remote MCP service, synchronize state through GitHub, or add a cloud persistence backend.

The Copilot adapter uses the native versioned hook format at:

```text
~/.copilot/hooks/djobs.json
```

It records these passive lifecycle events:

- session start;
- successful and failed tool results;
- pre-compaction state;
- real session end.

It deliberately does **not** install `UserPromptSubmit` or `Stop` automation. Ordinary prompts do not become tasks, and a model turn ending does not release ownership.

## Automatic observations versus explicit ownership

Automatic adapters may:

- load compact local state at session start without claiming it;
- record successful and failed tool outcomes;
- snapshot Git state and report changed paths;
- save a small marker before context compaction;
- heartbeat a task already claimed by the same session.

Automatic adapters never:

- turn every user prompt into a task;
- claim the newest pending task at startup;
- release work at every model `Stop` event;
- infer completion from natural-language output;
- overwrite another client's lease.

Ownership changes only through explicit operations:

```text
checkpoint(summary, path?, details?)
handoff(task_id, evidence, completed?)
```

Example:

```text
Copilot opens repository A
  -> SessionStart reads local tasks and observations
  -> checkpoint("Implement parser", path="src/parser.py")
  -> this Copilot session owns the explicit lease
  -> tool hooks record observations without creating tasks
  -> handoff(task_id, "Parser complete; edge tests remain")

Another local agent opens repository A
  -> sync_workspace reads the released task and Git observations
  -> checkpoint("Implement parser", path="src/parser.py")
  -> resumes the same task instead of creating a duplicate
```

If an agent disappears without a handoff, its lease eventually expires and normal local recovery makes the work available again.

## Optional local host adapters

Use a specific target only when that host runs independently from Copilot:

```powershell
djobs setup copilot
djobs setup codex
djobs setup claude
djobs setup gemini
djobs setup kimi
```

Configure every detected local host only when separate integrations are intentional:

```powershell
djobs setup all
```

| Host | Local user configuration | Passive events |
|---|---|---|
| GitHub Copilot CLI + VS Code Agent | `~/.copilot/hooks/djobs.json` | session start/end, tool success/failure, pre-compact |
| Codex | `~/.codex/hooks.json` | session start/end, tool result, pre-compact |
| Claude Code | `~/.claude/settings.json` | session start/end, tool success/failure, pre-compact |
| Gemini CLI | `~/.gemini/settings.json` | session start/end, after-tool, pre-compress |
| Kimi Code | `~/.kimi-code/config.toml` | one-time prompt context, session end, tool success/failure, pre-compact |

Kimi's MCP entry is merged into `~/.kimi-code/mcp.json`. Other hosts use their supported local MCP registration commands. Only djobs-managed entries are replaced or removed. Malformed settings are never overwritten automatically.

For Codex, review and trust newly installed local commands through `/hooks` when prompted.

Repair and remove also default to Copilot:

```powershell
djobs repair
djobs remove
djobs repair all
djobs remove kimi
```

## Any future or custom local agent

The normalized event entrypoint accepts any client identifier:

```bash
# The client sends its native hook JSON on stdin.
djobs agent-event session-start --client my-agent
djobs agent-event post --client my-agent
djobs agent-event post-failure --client my-agent
djobs agent-event pre-compact --client my-agent
djobs agent-event session-end --client my-agent
```

An adapter only maps native event names and payload fields to this command. Queue and ownership logic stay in the shared local core.

For a client with no hook mechanism, use the local Git sidecar:

```bash
djobs observe /path/to/repository --watch
```

The sidecar records real working-tree transitions. Its fingerprint includes tracked, staged, and bounded untracked content, so a second edit is detected even when `git status` still shows the same `M` state. Contents are hashed for comparison and are not stored as observation text.

MCP itself cannot force every possible client to call a tool at session start. The common local guarantees are the shared data format, Git observation fallback, MCP/CLI access, and explicit ownership semantics.

## Compact MCP tools

The default server exposes four tools:

| Tool | Purpose |
|---|---|
| `sync_workspace()` | Read tasks plus recent observations for the current repository under a token budget. It never claims work. |
| `checkpoint(summary, path?, details?)` | Deliberately create or resume and atomically claim one unit of work. |
| `handoff(task_id, evidence, completed?)` | Explicitly release or complete owned work with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Backward-compatible revision recovery for integrations already storing IDs. |

Lower-level queue tools remain available through `djobs-mcp-full`.

## Observation durability and privacy

Observations use their own schema and never masquerade as jobs.

- Snapshot compare-and-record is one immediate transaction, so concurrent clients do not duplicate the same repository transition.
- Each repository keeps at most 1,000 recent observations by default.
- Metadata remains valid JSON when bounded.
- Common bearer tokens, API keys, passwords, authorization values, and URL passwords are redacted on a best-effort basis.
- Stored task text and observations are untrusted data, never executable instructions.
- Hook, sidecar, or storage failure is fail-open and does not block coding.
- No observation or task state is uploaded by djobs.

## Repository detection

The resolver uses:

1. MCP client roots;
2. cwd supplied by a host, adapter, or event;
3. the enclosing Git repository root;
4. the process startup directory.

Starting in `repo/src/feature` resolves to `repo`. Windows, WSL, and common Git Bash spellings share one repository identity, while compatible aliases keep earlier path-based state readable.

## Local storage

Default database:

```text
~/.djobs/global.db
```

Override it with `DJOBS_DB`:

```powershell
$env:DJOBS_DB = "D:\state\team-djobs.db"
djobs-mcp
```

A repository-specific database is also supported:

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
