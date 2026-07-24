<p align="center">
  <img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/icon-128.png" width="96" alt="djobs logo">
</p>

<h1 align="center">djobs — Local Agent Memory</h1>

<p align="center">
  <strong>Continue the repository instead of explaining it again in every new AI session.</strong>
</p>

<p align="center">
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="Marketplace version" src="https://img.shields.io/visual-studio-marketplace/v/jhuang-tw.djobs"></a>
  <a href="https://marketplace.visualstudio.com/items?itemName=jhuang-tw.djobs"><img alt="Marketplace installs" src="https://img.shields.io/visual-studio-marketplace/i/jhuang-tw.djobs"></a>
  <a href="https://pypi.org/project/djobs/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/djobs.svg"></a>
  <img alt="Python 3.10–3.14" src="https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white">
  <img alt="Node.js 20+ for development" src="https://img.shields.io/badge/Node.js-20%2B-339933?logo=node.js&logoColor=white">
  <a href="https://github.com/jhuang-tw/djobs/blob/main/LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
</p>

![djobs — local project memory for coding agents](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

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
instead of replaying only the newest activity.

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

## Memory control

Ask Copilot naturally:

```text
What does djobs remember about the login bug?
Mark the old OAuth failure as resolved by this commit.
Forget the abandoned Redis approach.
Clear djobs memory for this repository.
```

Put `[djobs:no-memory]` in a prompt to skip that prompt. Set
`DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent memory globally.

## Requirements

| Component | Requirement |
|---|---|
| VS Code | 1.101 or newer |
| Python runtime | Python 3.10–3.14 |
| Extension development | Node.js 20+ |
| Storage | Local SQLite by default |
| Operating systems | Windows, macOS, Linux |

End users do not need Node.js. Node is only required to build or package the extension from source.

## Privacy

State stays on the user's machine. Common credentials are redacted on a best-effort basis, stored
memory is treated as untrusted data, hook failures are fail-open, unrelated settings are preserved,
and djobs does not upload repository memory or task state.

## Links

- [Full documentation and source](https://github.com/jhuang-tw/djobs)
- [PyPI package](https://pypi.org/project/djobs/)
- [Project website](https://jhuang-tw.github.io/djobs/)
- [Issues](https://github.com/jhuang-tw/djobs/issues)
