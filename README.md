# djobs

<!-- mcp-name: io.github.jhuang-tw/djobs -->

**Token-saving durable context for AI coding agents.** djobs gives Codex, Claude Code, Gemini, Copilot, Cursor, Cline, and any MCP-compatible coding agent durable, resumable task memory — so long, multi-file work survives crashes, context loss, or session interruptions without replaying completed work. It ships deterministic lifecycle hooks, context-efficient MCP tools, agent instructions, and a VS Code sidebar; setup is one command.

[![CI](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg)](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/djobs.svg)](https://pypi.org/project/djobs/)
[![Website](https://img.shields.io/badge/website-GitHub%20Pages-21835b.svg)](https://jhuang-tw.github.io/djobs/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

<p align="center">
  <img src="docs/demo.svg" alt="djobs demo — crash recovery in action" width="700">
</p>

---

## The Problem

AI coding agents (Codex, Claude Code, Gemini, GitHub Copilot, Cursor, Cline) often run multi-file tasks: add docstrings to 40 files, migrate a framework version, batch-refactor an API. These can take minutes.

When the IDE crashes, the chat disconnects, or you accidentally close the window — **all in-flight progress is lost.** The agent's state lives only in chat history. You spend tokens re-reading, re-planning, and guessing which files were already done.

## The Fix

djobs combines deterministic hooks for the common path with MCP tools for
structured, multi-step work:

| Layer | What it does |
|-------|-------------|
| Automatic `preToolUse` hook | Rewrites meaningful shell commands before execution and records a durable checkpoint without relying on the model |
| Automatic `sessionStart` hook | Injects unfinished and failed checkpoints into the next session |
| MCP workflow tools | Track semantic multi-file tasks with `enqueue_batch`, `complete_batch`, `resume_delta`, evidence, dependencies, and multi-agent claims |

```
You: "Add docstrings to all 20 files in src/"

  djobs hook checkpoints meaningful commands automatically
  Agent edits file 1 → structured MCP task completes ✅
  Agent edits file 2 → structured MCP task completes ✅
  ...
  Agent reaches file 12
  💥 VS Code crashes

You reopen VS Code, start a new chat: "hi"

  sessionStart injects the remaining work automatically
  Agent continues with file 13                        ✅
  ...
  Agent edits file 20 → complete_task     ✅
  Done — zero files lost, zero files re-done.
```

Everything is stored in a local SQLite file. No Redis, no Docker, no cloud service.

See the value directly with `djobs gain`. It reports estimated tokens saved over the last 24 hours, 30 days, and all time, split between automatic command checkpoints and structured workflows. The estimate is explicit and exportable rather than presented as provider billing data.

## Compatibility Status

Current implementation and day-to-day testing are done with **GitHub Copilot in
VS Code**. djobs is designed around MCP, so it should apply to any
MCP-compatible coding-agent host, but the non-Copilot hosts below still need
broader real-world validation.

| Agent / host | Status |
|--------------|--------|
| GitHub Copilot in VS Code | Implemented and tested through the VS Code extension + MCP registration. |
| Claude Code | Intended via `djobs init` / MCP config; not yet fully end-to-end tested. |
| Cursor | Intended via `djobs init` / MCP config; not yet fully end-to-end tested. |
| Cline | Intended via `djobs init` / MCP config; not yet fully end-to-end tested. |
| Codex | Intended when used through an MCP-capable client; not yet fully end-to-end tested. |
| Gemini | Intended when used through an MCP-capable client; not yet fully end-to-end tested. |
| Plain browser chat without tools | Not automatic; djobs needs MCP/tool access or installed agent guidance. |

> **How is this different from Celery / RQ / Dramatiq?** Those are general-purpose task queues
> built for backend workers and high throughput. djobs is purpose-built for **AI coding agents**:
> it speaks MCP natively, optimizes for crash-recovery and human-inspectable audit trails over raw
> throughput, and runs with zero infrastructure — one local SQLite file, no broker, no daemon.

> **How is this different from agent memory / RAG tools?** Memory tools help an agent *recall
> knowledge* — they summarize past sessions (often with an extra LLM step), store the summaries in
> a vector database, and retrieve relevant snippets back into context. djobs tracks *work state*:
> which tasks are done, which remain, and the evidence recorded for each. It checkpoints and
> resumes with **no LLM calls, no embeddings, and no background service** — just one local SQLite
> file — so recovery is exact and auditable rather than approximate. The two are complementary: one
> helps an agent *remember*, djobs helps it *finish and prove* the work.

> **Maturity — early but tested.** CI covers Python 3.11–3.14, SQLite and optional PostgreSQL
> backends. Marked Alpha while the public API stabilizes; the core checkpoint → resume flow is
> stable and used daily.

---

## Quick Start

> **djobs is workflow state for your AI agent — not a dependency of your app.**
> It works in any repo (Python, JS, Go, Rust, docs) because the queue is a tool
> the agent uses, not a library your project imports. djobs installs and manages
> its own runtime; you just pick how to set it up.

### 1. VS Code / GitHub Copilot (easiest)

Install the
**[djobs extension](https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs)**
from the Marketplace, then run **`djobs: Set up / Repair djobs`** from the
Command Palette (or click **Set up djobs** when the extension offers).

That one step installs the runtime, wires the MCP server, installs deterministic
lifecycle hooks and agent instructions, and adds the task sidebar. No manual config.

After setup, you keep talking normally — “continue”, “fix this”, “run tests”,
“retry”, “the previous run failed”, or “release” are enough. The extension does
not generate, copy, or open Chat prompts. Meaningful terminal commands are
checkpointed before execution, failed checkpoints are restored at the next
session, and MCP remains available for semantic multi-step workflows.

### 2. Any MCP agent (Codex, Claude Code, Gemini, Cursor, Cline, …)

One command wires the current project for any MCP-compatible agent:

```bash
djobs init
```

It writes `.vscode/mcp.json`, installs `.github/hooks/djobs.json`, installs the
agent guidance block in `.github/copilot-instructions.md`, runs `djobs doctor`,
and prints next steps. It auto-detects the right interpreter, so the wiring works
even in a JavaScript, Go, or Rust repo with no Python environment.

> **djobs does not rely on the model remembering.** Deterministic hooks handle
> command checkpointing and session recovery. Agent instructions and MCP tools
> add richer file-level planning, evidence, dependencies, and multi-agent state.

<details>
<summary>Installing the djobs runtime (only if setup asks)</summary>

The VS Code extension installs and repairs the runtime for you. If you are
setting up outside VS Code, install it once — like `git` or `ripgrep`, globally,
not per project:

```bash
pipx install djobs   # isolated global install (recommended)
# no pipx? ->  pip install djobs   (or: python -m pip install --user djobs)
```

Then run `djobs init` in any project.
</details>

### Granular commands

`djobs init` is the recommended path, but each step is also available on its own:

```bash
djobs install-mcp           # write only .vscode/mcp.json
djobs install-instructions  # write only the agent guidance block
djobs hook install          # write only .github/hooks/djobs.json
djobs gain                  # show 24h / 30d / all-time token savings
djobs doctor                # diagnose an existing setup
```

Verify the setup at any time:

```bash
djobs doctor
# [OK  ] djobs package: v0.10.0 ...
# [OK  ] queue db (global default): ~/.djobs/global.db — exists, writable
# [OK  ] mcp.json wiring: command='djobs-mcp' — found
# [OK  ] agent guidance block: present in .github/copilot-instructions.md
# [OK  ] automatic command hooks: installed at .github/hooks/djobs.json
```

<details>
<summary>Options and manual setup</summary>

```bash
# One-command setup with full auto-approve (agent can enqueue/complete/fail without prompts)
djobs init --full-approve

# Re-run setup, overwriting an existing mcp.json
djobs init --force

# Also write .agent.md (for agents that read it) in addition to copilot-instructions
djobs init --instructions-target all

# Just the wiring, safe default (read-only tools auto-approved)
djobs install-mcp

# Or wiring with full auto-approve
djobs install-mcp --full-approve

# Just the agent guidance block (no mcp.json changes)
djobs install-instructions                 # -> .github/copilot-instructions.md
djobs install-instructions --target agent-md  # -> .agent.md
djobs install-instructions --target all       # -> both
djobs install-instructions --print            # print the block, write nothing
```

Or add to `.vscode/mcp.json` manually. After a global install the `djobs-mcp`
console script is on your PATH, so the wiring is identical on every OS:

```json
{
  "servers": {
    "djobs": {
      "type": "stdio",
      "command": "djobs-mcp",
      "autoApprove": [
        "health", "resume_session", "check_task", "list_tasks", "audit_log"
      ]
    }
  }
}
```

<details>
<summary>Per-project venv (portable, for repos that commit mcp.json)</summary>

If you'd rather keep djobs inside each project's virtual environment — e.g. so a
checked-in `mcp.json` resolves to whatever `.venv` each collaborator has — run
`djobs install-mcp --portable` to emit a relocatable interpreter hint:

```json
{
  "servers": {
    "djobs": {
      "type": "stdio",
      "command": "${workspaceFolder}/.venv/Scripts/python",
      "args": ["-m", "djobs.mcp_server"],
      "autoApprove": [
        "health", "resume_session", "check_task", "list_tasks", "audit_log"
      ]
    }
  }
}
```

On macOS / Linux the path is `${workspaceFolder}/.venv/bin/python`. You can also
pin any interpreter explicitly with `djobs install-mcp --python /path/to/python`.
</details>

> **Security note:** The default `autoApprove` list only includes **read-only** tools.
> If you want your agent to enqueue/complete/fail tasks without confirmation prompts, add
> `"enqueue_task"`, `"complete_task"`, and `"fail_task"` to the array — but understand that
> this allows the agent to mutate queue state without asking.

</details>

---

## Automatic Command Rewriting

`djobs init` installs a deterministic `preToolUse` hook. Before a meaningful
Bash or PowerShell command runs, the hook substitutes a `djobs hook run`
wrapper through the host's supported tool-argument mutation API. The original
command output and exit code are preserved.

The default **smart** mode checkpoints tests, builds, linters, type checks, and
other substantial compound commands. It skips read-only commands such as
`git status` and shell-state-only commands such as `cd` or `export`.

```bash
djobs hook install --mode smart   # recommended
djobs hook install --mode all     # checkpoint almost every terminal command
djobs hook install --mode off     # install hooks but disable rewriting
djobs hook install --global       # share ~/.djobs/global.db with MCP
djobs hook doctor                 # validate the hook file
```

Hook processing is fail-open: if djobs cannot inspect or checkpoint a command,
it returns control to the host instead of blocking the user's work. `djobs pause`
disables both automatic rewriting and session-start recovery without deleting data.

Successful automatic command checkpoints are archived after their audit evidence
is recorded, keeping the active sidebar clean. Failed or interrupted checkpoints
remain visible and are injected into the next session automatically.


### Token Savings Analytics

Like RTK's `gain` view, djobs makes its value visible instead of asking users to
trust a marketing percentage:

```bash
djobs gain                         # current workspace: 24h / 30d / all time
djobs gain --graph                 # 30-day ASCII graph
djobs gain --history               # recent records and their estimated savings
djobs gain --daily                 # non-empty day-by-day totals
djobs gain --all --format json     # every workspace, machine-readable export
```

`djobs stats` and `djobs state` are aliases for the same report.

The report separates **automatic hook savings** from **durable workflow savings**
and also shows unfinished or failed checkpoints whose compact context is protected
for recovery. Numbers estimate avoided replay, re-reading, and re-planning using a
published formula (`4` characters per token and `600` re-plan tokens per completed
record by default). They are intentionally labeled estimates, not API billing data.

---

## See It In Action

The animated SVG above shows the full demo. To run it yourself:

```bash
pip install djobs
# if you cloned the repo:
python examples/run_migration_demo.py
```

20 files enqueued → 12 completed → crash → resume → 8 remaining finished. Zero data loss.

---

## What Else Can It Do?

Beyond the three core tools, djobs also provides:

- **`audit_log`** — "What did the AI do yesterday?" Full event history across sessions.
- **`work_receipt`** — An evidence-backed receipt of what was done: changed files, completed tasks with their evidence, what remains, and a recommended next step. When run inside a git repository it also folds in ground truth from git — how many files git actually sees as changed, and any file a task *claimed* but git shows no pending change for — so the claims can be cross-checked against the repository. Read-only, so the next session (or a reviewer) can trust progress without re-reading the chat. CLI: `djobs receipt --correlation-id <workspace>` (add `--no-git` to skip the git check).
- **`check_task` / `list_tasks`** — Inspect individual tasks or list by workspace.
- **`health`** — Queue depth by status at a glance.
- **`djobs doctor`** — One-shot setup check: confirms djobs is installed, the queue DB is writable, and `.vscode/mcp.json` is wired correctly. Run it (or "djobs: Diagnose Setup" in VS Code) whenever something feels off.
- **`djobs gain`** — RTK-style 24h / 30d / all-time savings analytics with source
  breakdowns, daily history, an ASCII graph, and JSON export. The older
  `djobs token-savings` command remains available for one-workflow estimates.
- **Multi-agent coordination** — Several agents share one queue: `claim_task` (atomic, exclusive), `heartbeat_task`, `release_task`, task dependencies (`depends_on`), resource locks (`resource_key`), and an agent registry (`register_agent` / `agent_heartbeat` / `list_agents`).
- **Web dashboard** — `djobs dashboard` serves a read-only, cross-agent view of queue health, every task, and the live agent fleet at `http://127.0.0.1:8787` (stdlib only, no extra deps). **Local-only by design:** no authentication, binds to `127.0.0.1`; for remote access use an SSH tunnel rather than exposing a public interface.
- **Retry with backoff** — Failed tasks can retry automatically.
- **Dead letter queue** — Tasks that exhaust all retries are preserved for review.

For the full architecture, Python library API, PostgreSQL backend, configuration reference, and comparison with other tools, see [docs/INTERNALS.md](docs/INTERNALS.md).

---

## Development

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv && .venv/bin/activate
pip install -e ".[dev]"

pytest -q              # tests
ruff check src/ tests/ # lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## VS Code Extension

djobs includes a VS Code sidebar extension for visual workflow control:

- **Workflow dashboard** — tasks grouped by workflow and action type with progress indicators
- **Native MCP + hook setup** — registers the MCP server and deterministic lifecycle hooks without manual config
- **Agent guidance installer** — teaches compatible agents to resume first and
  create durable djobs tasks before multi-step edits
- **Task cleanup controls** — right-click a task to archive it, delete it, view
  audit history, copy its ID, or inspect raw JSON
- **Pause switch** — `djobs pause` / `djobs unpause` (or the sidebar Pause/Resume
  button) temporarily stop agents resuming/enqueueing when a stuck task keeps
  pulling the agent back; nothing is deleted
- **Optional prompt actions** — off by default; enable `djobs.promptActions.enabled`
  to show a manual prompt action for finishing a workflow in Chat
- **Evidence trail** — see what the agent actually changed in each completed task

Install the VSIX from `vscode-ext/` or build it yourself:

```bash
cd vscode-ext && npm install && npm run package
# Install: code --install-extension djobs-X.Y.Z.vsix
```

---

## Roadmap

- [x] Durable workflow state (`enqueue` → `complete` → `resume`)
- [x] Audit trail — "what did the AI do?"
- [x] MCP server with 15 tools
- [x] `pip install djobs && djobs install-mcp` — two-command setup
- [x] Published on PyPI
- [x] `complete_task` evidence field — agent records what it changed
- [x] VS Code sidebar — workflow dashboard, skip/archive, inspect evidence
- [x] Deterministic lifecycle hooks — rewrite meaningful commands and resume failed/interrupted checkpoints
- [x] Agent guidance installer — adds semantic multi-file workflow guidance
- [x] CLI workflow control — `djobs skip`, `djobs accept-before`, `djobs archive-workflow`
- [x] Multi-agent coordination — shared-queue claim, dependencies, resource locks, agent registry
- [x] Web dashboard — `djobs dashboard` cross-agent global view
- [x] Published on VS Code Marketplace
- [ ] Status bar badge + notification alerts

---

## License

[MIT](LICENSE)

