# djobs

![djobs - crash-proof task memory for AI coding agents](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Crash-proof workflow state for your AI coding agent.** djobs gives Copilot (and
any MCP agent) durable, resumable task memory so long, multi-file work survives
IDE crashes, context loss, and closed chats - nothing lost, nothing redone.

This extension is the one-click way to set djobs up: it installs and manages the
runtime for you, wires the MCP server, installs the agent instructions, and adds
a task sidebar. You do not manage Python or config by hand.

## Get started

1. Install this extension.
2. Run **djobs: Set up / Repair djobs** from the Command Palette (or click
   **Set up djobs** when the extension offers).
3. Start a new agent session - it will resume unfinished work automatically.
   For new multi-step work, run **djobs: Start Tracked Workflow** so the agent
   resumes first, then creates djobs tasks before editing.

The setup step installs the runtime if needed, registers the MCP server,
installs the agent guidance block, and verifies everything with `djobs doctor`.
Use **djobs: Diagnose Setup** anytime to re-check.

## What the sidebar shows

- djobs tasks grouped by workflow and status.
- Stale (long-unfinished) and blocked tasks flagged, with one-click archive.
- Copy task IDs and open task JSON for inspection.
- Resume and Start Workflow commands that hand your agent ready-to-use prompts.
- When the current workspace has no active tasks, the empty state offers a
   tracked-workflow prompt instead of leaving the sidebar looking broken.

## Advanced

The extension drives the `djobs` CLI under the hood. If you prefer to manage the
runtime yourself, install it once globally (`uv tool install djobs` or
`pipx install djobs` with Python 3.11+) and run `djobs init` in a project; the
extension will detect and use it. If djobs lives in a non-default interpreter,
set `djobs.pythonPath` in VS Code settings.
