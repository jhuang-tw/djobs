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

```bash
pip install djobs
djobs install-mcp
```

Two commands. Your AI agent now has crash-proof task memory.

Verify it works:

```bash
djobs health
# {"pending": 0, "running": 0, "succeeded": 0, ...}
```

<details>
<summary>Options and manual setup</summary>

```bash
# Safe default (read-only tools auto-approved)
djobs install-mcp

# Or with full auto-approve (agent can enqueue/complete/fail without prompts)
djobs install-mcp --full-approve
```

Or add to `.vscode/mcp.json` manually:

<!-- Windows (venv) -->
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

<details>
<summary>macOS / Linux (venv)</summary>

```json
{
  "servers": {
    "djobs": {
      "type": "stdio",
      "command": "${workspaceFolder}/.venv/bin/python",
      "args": ["-m", "djobs.mcp_server"],
      "autoApprove": [
        "health", "resume_session", "check_task", "list_tasks", "audit_log"
      ]
    }
  }
}
```
</details>

<details>
<summary>System Python (any OS)</summary>

```json
{
  "servers": {
    "djobs": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "djobs.mcp_server"],
      "autoApprove": [
        "health", "resume_session", "check_task", "list_tasks", "audit_log"
      ]
    }
  }
}
```
</details>

> **Security note:** The default `autoApprove` list only includes **read-only** tools.
> If you want your agent to enqueue/complete/fail tasks without confirmation prompts, add
> `"enqueue_task"`, `"complete_task"`, and `"fail_task"` to the array — but understand that
> this allows the agent to mutate queue state without asking.

</details>

---

## Making Your Agent Use djobs Automatically

After installing, your agent has the MCP tools available — but it won't use them unless you tell it to. The easiest way is to use the included **Durable Coder** agent definition:

**Option A: Copy to your project (recommended)**

```bash
# Copy the agent file to your project root
cp $(python -c "import djobs, pathlib; print(pathlib.Path(djobs.__file__).parent.parent.parent / '.agent.md')") .agent.md
```

Or just copy [`.agent.md`](.agent.md) from this repo into your project root.

**Option B: Use the GitHub Copilot agent picker**

If you cloned this repo, the agent is already at `.github/agents/durable-coder.agent.md`. In VS Code, select "Durable Coder" from the agent picker in Copilot Chat.

**What the agent does:**
- On every new chat, silently calls `resume_session` to find unfinished work
- For multi-file tasks (>3 files), automatically enqueues each file as a durable task
- After editing each file, calls `complete_task` to record progress
- If a session crashes, the next chat auto-resumes from where it stopped — no questions asked

You can also write your own `.agent.md` or add djobs tool calls to any existing agent instructions.

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
