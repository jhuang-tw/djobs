# djobs — Agent Checkpoints

![djobs agent checkpoints](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Crash-proof checkpoints and resumable task memory for AI coding agents.**

This extension is the one-click setup and task view for djobs. It installs or
repairs the runtime, registers the MCP server, installs deterministic lifecycle
hooks, runs diagnostics, and shows recoverable work in the sidebar.

## Setup

1. Install the extension.
2. Run **djobs: Set up / Repair djobs** from the Command Palette.
3. Start a new compatible agent session and work normally.

Meaningful terminal commands are checkpointed before execution. Failed or
interrupted checkpoints can be injected into the next session; successful
checkpoints remain auditable without filling the active task view.

## What the extension provides

- Automatic `preToolUse` command checkpointing and `sessionStart` recovery setup.
- MCP server registration for structured multi-file workflows.
- Current-workspace and global queue views.
- Pause/resume, archive, delete, history, evidence, and setup diagnostics.
- Optional prompt actions, disabled by default.
- Local SQLite storage by default; no hosted service required.

## See the savings

Run in the integrated terminal:

```bash
djobs gain
djobs gain --graph
djobs gain --history
djobs gain --all --format json
```

The report separates automatic checkpoints from structured workflows and labels
its numbers as estimates rather than provider billing data.

## Compatibility

Automatic hooks, setup, and the sidebar are implemented and tested with GitHub
Copilot in VS Code. MCP workflows can be used by other MCP-compatible hosts;
automatic behavior depends on each host's hook protocol and still needs broader
real-world validation.

## Privacy and control

Queue data is local unless you configure a shared database. Default MCP write
actions are conservative. **Pause djobs** disables rewriting and recovery without
deleting state. Prompt actions remain opt-in.

For commands, architecture, and troubleshooting, use the repository
[README](https://github.com/jhuang-tw/djobs). Report issues in the repository's
issue tracker.
