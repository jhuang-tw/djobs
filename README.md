# djobs

**Crash-proof task memory for AI coding agents.** When your AI agent is halfway through editing 20 files and VS Code crashes, djobs makes sure it picks up from file 13 instead of starting over.

[![CI](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg)](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

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

---

## Quick Start

```bash
pip install djobs
djobs install-mcp
```

Two commands. Your AI agent now has crash-proof task memory.

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

After installing, your agent has the MCP tools available — but it won't use them unless you tell it to. The easiest way is to copy the **Durable Coder** agent definition from this repo:

**Option A: Copy to your project (recommended)**

Copy [`.agent.md`](.agent.md) from this repo into your project root.

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

```bash
# Codebase migration demo — crash mid-way, resume, zero data loss
python examples/run_migration_demo.py
```

The demo scans 20 Python files, enqueues each as an "add-docstrings" task, completes 12, simulates an IDE crash, then calls `resume_session` and finishes the remaining 8.

---

## What Else Can It Do?

Beyond the three core tools, djobs also provides:

- **`audit_log`** — "What did the AI do yesterday?" Full event history across sessions.
- **`check_task` / `list_tasks`** — Inspect individual tasks or list by workspace.
- **`health`** — Queue depth by status at a glance.
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

pytest -q              # 214 tests (16 skipped without Postgres)
ruff check src/ tests/ # lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Roadmap

- [x] Crash-proof task memory (`enqueue` → `complete` → `resume`)
- [x] Audit trail — "what did the AI do?"
- [x] MCP server with 8 tools
- [x] `pip install djobs && djobs install-mcp` — two-command setup
- [x] Published on PyPI
- [x] `complete_task` evidence field — agent records what it changed
- [ ] VS Code sidebar — visual task progress and one-click resume

---

## License

[MIT](LICENSE)
