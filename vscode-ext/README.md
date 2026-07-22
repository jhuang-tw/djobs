# djobs — Coding Checkpoints

![djobs coding checkpoints](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Automatic coding checkpoints that reduce repeated context, re-reading, and command work.**

The extension is intentionally headless. It does not add an Activity Bar icon, task
sidebar, polling loop, or background dashboard. It installs or repairs the djobs
runtime, registers the MCP server, and installs deterministic coding hooks.

## Setup

1. Install the extension.
2. Run **djobs: Set up / Repair djobs** from the Command Palette.
3. Start a new compatible coding-agent session and work normally.

Smart hooks checkpoint meaningful tests, builds, linters, type checks, and compound
terminal commands before execution. Failed or interrupted work can be restored in
the next session without asking the model to reconstruct the entire conversation.

## Commands

- **djobs: Set up / Repair djobs** — install/update the engine, native MCP registration, and smart hooks.
- **djobs: Diagnose Setup** — verify runtime, MCP, queue, and hook health.
- **djobs: Pause djobs** — temporarily disable automatic rewriting and recovery.
- **djobs: Resume djobs** — re-enable automation.

There is no task-management UI. Detailed inspection remains available through the
CLI and MCP tools only when needed.

## See the estimated savings

```bash
djobs gain
djobs gain --graph
djobs gain --history
djobs gain --all --format json
```

The report separates automatic checkpoints from structured workflows and labels
its values as estimates rather than provider billing data.

## Compatibility

Automatic hooks, native MCP registration, setup, and diagnostics are implemented
and tested with GitHub Copilot in VS Code. Other MCP-compatible coding agents can
use the core tools; automatic behavior depends on each host's hook protocol.

## Privacy and control

Queue data stays local unless you configure a shared database. `djobs pause`
disables automation without deleting state. The extension performs no task polling
and adds no persistent VS Code view.
