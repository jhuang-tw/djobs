# djobs — Local Agent Memory

![djobs local agent memory](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Local repository memory, passive observations, and explicit handoff for coding agents.**

The extension is intentionally headless. It does not add an Activity Bar icon, task
sidebar, polling loop, background dashboard, remote service, or cloud database.

## Setup

1. Install the extension.
2. Run **djobs: Set up / Repair djobs** from the Command Palette.
3. Start a new Copilot or VS Code Agent session.

Setup installs or upgrades the local Python package, registers the compact MCP server
through VS Code's native provider, and installs the passive Copilot lifecycle adapter.
The adapter records session, tool-result, compaction, and session-end observations in
local SQLite. It does not turn prompts or commands into tasks.

## Four compact tools

- `sync_workspace()` reads repository tasks and recent observations without claiming work.
- `checkpoint(...)` deliberately creates or resumes one task and claims its lease.
- `handoff(...)` explicitly releases or completes owned work with bounded evidence.
- `resume_delta(...)` preserves compatibility for integrations already storing revision IDs.

Lower-level queue and administration tools remain available through `djobs-mcp-full`,
not in every ordinary VS Code Agent context.

## Commands

- **djobs: Set up / Repair djobs** — install or update the engine, passive hook, and native MCP registration.
- **djobs: Diagnose Setup** — verify runtime, MCP, local database, and hook health.
- **djobs: Pause djobs** — temporarily disable djobs operations without deleting state.
- **djobs: Resume djobs** — re-enable djobs.

## Compatibility

The extension's native MCP provider and passive Copilot hook document are covered by
automated tests. Codex, Claude Code, Gemini CLI, Kimi Code, and custom local agents can
use the same core through their optional adapters. Real host installation still depends
on the host version and local environment, so diagnostics remain available.

## Privacy and control

State stays on the user's machine. The default shared database is
`~/.djobs/global.db`, and a workspace-specific path is optional. Hook failures are
fail-open, unrelated settings are preserved, and no observation or task state is
uploaded by djobs.
