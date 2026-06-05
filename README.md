# djobs

**Durable workflow control for AI coding agents.** When your AI agent runs a multi-file task, djobs makes every step inspectable, resumable, and controllable — and survives IDE crashes, context loss, or session interruptions.

[![CI](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg)](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/djobs.svg)](https://pypi.org/project/djobs/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

<p align="center">
  <img src="docs/demo.svg" alt="djobs demo — crash recovery in action" width="700">
</p>

---

## The Problem

AI coding agents (GitHub Copilot, Cursor, Cline, Claude Code) often run multi-file tasks: add docstrings to 40 files, migrate a framework version, batch-refactor an API. These can take minutes.

When the IDE crashes, the chat disconnects, or you accidentally close the window — **all in-flight progress is lost.** The agent's state lives only in chat history. You have to start over and guess which files were already done.

## The Fix

djobs gives your agent three tools that solve this:

| Tool | What it does |
|------|-------------|
| `enqueue_task` | Save each file as a durable task — survives any crash |
| `complete_task` | Mark a file done after the agent edits it |
| `resume_session` | On next chat, find all unfinished files instantly |

```
You: "Add docstrings to all 20 files in src/"

  Agent calls enqueue_task for each file  ← checkpoint saved
  Agent edits file 1 → complete_task      ✅
  Agent edits file 2 → complete_task      ✅
  ...
  Agent edits file 12 → complete_task     ✅
  💥 VS Code crashes

You reopen VS Code, start a new chat: "hi"

  Agent calls resume_session              ← finds 8 incomplete tasks
  Agent edits file 13 → complete_task     ✅
  ...
  Agent edits file 20 → complete_task     ✅
  Done — zero files lost, zero files re-done.
```

Everything is stored in a local SQLite file. No Redis, no Docker, no cloud service.

> **How is this different from Celery / RQ / Dramatiq?** Those are general-purpose task queues
> built for backend workers and high throughput. djobs is purpose-built for **AI coding agents**:
> it speaks MCP natively, optimizes for crash-recovery and human-inspectable audit trails over raw
> throughput, and runs with zero infrastructure — one local SQLite file, no broker, no daemon.

> **Maturity — early but tested.** 291 passing tests, CI across Python 3.11–3.13, SQLite and optional
> PostgreSQL backends. Marked Alpha while the public API stabilizes; the core enqueue → complete →
> resume flow is stable and used daily.

---

## Quick Start

> **djobs is a tool, not a project dependency.** Install it **once**, globally —
> like `git` or `ripgrep` — and it works in every project, with or without a
> virtual environment. You do **not** add it to each project's `requirements.txt`
> or create a `.venv` for it.

```bash
pipx install djobs   # isolated global install (recommended)
djobs install-mcp    # wire it into the current project's VS Code agent
```

No `pipx`? `pip install djobs` works too (`python -m pip install --user djobs`
for a user-level install). `install-mcp` auto-detects the right interpreter, so
the wiring keeps working even in a JavaScript, Go, or Rust repo with no Python
environment.

That's it — your AI agent now has crash-proof task memory.

**Prefer the VS Code UI?** Install the
[djobs extension](https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs)
and click **Set up djobs** when it offers — it runs the two steps above for you
and adds a task sidebar.

Verify the setup at any time:

```bash
djobs doctor
# [OK  ] djobs package: v0.6.5 ...
# [OK  ] queue db (global default): ~/.djobs/global.db — exists, writable
# [OK  ] mcp.json wiring: command='djobs-mcp' — found
```

<details>
<summary>Options and manual setup</summary>

```bash
# Safe default (read-only tools auto-approved)
djobs install-mcp

# Or with full auto-approve (agent can enqueue/complete/fail without prompts)
djobs install-mcp --full-approve
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

## Making Your Agent Use djobs Automatically

After installing, your agent has the MCP tools available — but it won't use them unless you tell it to. Add the following to your agent instructions (e.g. `.github/copilot-instructions.md` or any `.agent.md`):

```
At the start of every session, call resume_session to find unfinished work.
For multi-file tasks, enqueue each file as a durable task and call complete_task after each edit.
```

**What this gives you:**
- On every new chat, unfinished work is automatically surfaced
- For multi-file tasks (>3 files), each file is tracked as a durable task
- After editing each file, progress is recorded
- If a session crashes, the next chat auto-resumes from where it stopped — no questions asked

You can also use the `djobs install-instructions` CLI command to add the guidance block automatically.

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
- **`check_task` / `list_tasks`** — Inspect individual tasks or list by workspace.
- **`health`** — Queue depth by status at a glance.
- **`djobs doctor`** — One-shot setup check: confirms djobs is installed, the queue DB is writable, and `.vscode/mcp.json` is wired correctly. Run it (or "djobs: Diagnose Setup" in VS Code) whenever something feels off.
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

pytest -q              # 291 tests (18 skipped without Postgres)
ruff check src/ tests/ # lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## VS Code Extension

djobs includes a VS Code sidebar extension for visual workflow control:

- **Workflow dashboard** — tasks grouped by workflow and action type with progress indicators
- **Resume from any step** — expand a workflow, pick a file, and the previous steps are auto-accepted
- **Skip / Archive** — mark tasks done without editing, or archive stale workflows
- **Evidence trail** — see what the agent actually changed in each completed task

Install the VSIX from `vscode-ext/` or build it yourself:

```bash
cd vscode-ext && npm install && npm run package
# Install: code --install-extension djobs-0.6.0.vsix
```

---

## Roadmap

- [x] Durable workflow state (`enqueue` → `complete` → `resume`)
- [x] Audit trail — "what did the AI do?"
- [x] MCP server with 14 tools
- [x] `pip install djobs && djobs install-mcp` — two-command setup
- [x] Published on PyPI
- [x] `complete_task` evidence field — agent records what it changed
- [x] VS Code sidebar — workflow dashboard, resume from any step, skip/archive
- [x] CLI workflow control — `djobs skip`, `djobs accept-before`, `djobs archive-workflow`
- [x] Multi-agent coordination — shared-queue claim, dependencies, resource locks, agent registry
- [x] Web dashboard — `djobs dashboard` cross-agent global view
- [x] Published on VS Code Marketplace
- [ ] Status bar badge + notification alerts

---

## License

[MIT](LICENSE)
