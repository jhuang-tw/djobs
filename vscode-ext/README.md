<p align="center">
  <img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/icon-128.png" width="88" alt="djobs logo">
</p>

<h1 align="center">djobs — Local Agent Memory</h1>

<p align="center">
  <strong>Local project memory and explicit handoff for AI coding agents.</strong><br>
  Continue the repository instead of explaining it again in every new AI session.
</p>

<p align="center">
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="Marketplace version" src="https://img.shields.io/visual-studio-marketplace/v/jhuang-tw.djobs"></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="Marketplace installs" src="https://img.shields.io/visual-studio-marketplace/i/jhuang-tw.djobs"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/djobs.svg"></a>
  <img alt="Python 3.10–3.14" src="https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white">
  <img alt="Node.js 20+ for development" src="https://img.shields.io/badge/Node.js-20%2B-339933?logo=node.js&logoColor=white">
  <a href="https://github.com/jhuang-tw/djobs/blob/main/LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
</p>

## What the extension does

The extension is the one-click VS Code route for djobs. It:

- registers the local MCP server for GitHub Copilot and VS Code Agent;
- installs or repairs the Python runtime and passive lifecycle hooks;
- diagnoses MCP, database, runtime, and hook health;
- pauses and resumes djobs without deleting local state;
- keeps repository memory on the user's machine.

It does **not** add a permanent sidebar, dashboard, polling loop, remote service, or cloud database.
The UI stays out of the way after setup.

## What djobs remembers

- the user's bounded, redacted goal and important constraints;
- successful and failed tool results;
- actual Git working-tree changes;
- a compact session capsule before context compaction or exit;
- explicit task ownership and handoff evidence when coordinated work needs it.

When the next request arrives, `sync_workspace(query=...)` searches relevant repository memory
instead of replaying an entire chat history.

## How it works

1. **Capture the session.** Bounded intent, tool results, failures, Git changes, and a session
   capsule are stored as local data.
2. **Search for the current request.** `sync_workspace(query=...)` retrieves relevant repository
   memory under a token budget.
3. **Continue with evidence.** The agent resumes useful state and uses explicit checkpoint or
   handoff only when coordinated work needs ownership.

## Get started

1. Install this extension.
2. Open a Git repository.
3. Start using Copilot normally.

The first djobs MCP call creates `~/.djobs/global.db` and installs the passive Copilot lifecycle
adapter. There is no required per-project command.

Run **djobs: Set up / Repair djobs** only when Python is missing, an old launch path needs repair,
or diagnostics report a damaged installation.

## Five compact MCP tools

| Tool | Purpose |
|---|---|
| `sync_workspace(query?, known_context_hash?, ...)` | Recover relevant goals, failures, capsules, task state, and Git changes under a token budget. |
| `memory(action, ...)` | List, search, deactivate, forget, or explicitly clear passive repository memory. |
| `checkpoint(summary, ...)` | Deliberately create or resume one checkout-scoped unit of work. |
| `handoff(task_id, ...)` | Release or complete tracked work with bounded evidence. |
| `resume_delta(correlation_id, ...)` | Compatibility path for integrations that already persist revision IDs. |

Passive memory never silently creates or claims tasks. Lower-level queue and administration tools
remain available through `djobs-mcp-full` rather than occupying every ordinary Agent context.

## Commands

- **djobs: Set up / Repair djobs** — install or update the engine, hooks, and native MCP registration.
- **djobs: Diagnose Setup** — verify runtime, MCP, local database, and hook health.
- **djobs: Pause djobs** — temporarily disable djobs operations without deleting state.
- **djobs: Resume djobs** — re-enable djobs.

## Requirements

| Component | Requirement |
|---|---|
| VS Code | 1.101 or newer |
| Python runtime | Python 3.10+; Python 3.10–3.14 tested in CI |
| Extension development | Node.js 20+ |
| Storage | Local SQLite by default |
| Operating systems | Windows, macOS, Linux |

End users do not need Node.js. Node is required only to build or package the extension.

## Privacy and control

- State defaults to `~/.djobs/global.db`.
- Common credentials are redacted on a best-effort basis.
- Add `[djobs:no-memory]` to skip one prompt.
- Set `DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent capture.
- Mark memory resolved, superseded, stale, or contradicted without erasing its audit trail.
- Hook failures are fail-open, unrelated settings are preserved, and djobs does not upload
  repository memory or task state.

<p align="center">
  <a href="https://github.com/jhuang-tw/djobs"><strong>Source</strong></a>
  &nbsp;·&nbsp;
  <a href="https://pypi.org/project/djobs/"><strong>PyPI</strong></a>
  &nbsp;·&nbsp;
  <a href="https://jhuang-tw.github.io/djobs/"><strong>Documentation</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/jhuang-tw/djobs/issues"><strong>Issues</strong></a>
</p>
