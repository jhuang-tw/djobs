# djobs

![djobs - crash-proof task memory for AI coding agents](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Token-saving durable context for your AI coding agent.** djobs gives Codex,
Claude, Gemini, Copilot, Cursor, Cline, and any MCP-compatible agent durable,
resumable task memory so long, multi-file work survives IDE crashes, context
loss, and closed chats - nothing lost, nothing redone, fewer tokens replaying
finished work.

This extension is the one-click way to set djobs up: it installs and manages the
runtime for you, wires the MCP server, installs the agent instructions, and adds
a task sidebar. You do not manage Python or config by hand.

## Get started

1. Install this extension.
2. Run **djobs: Set up / Repair djobs** from the Command Palette (or click
   **Set up djobs** when the extension offers).
3. Start a new agent session and keep talking normally.

The extension does not generate, copy, or open Chat prompts. It wires MCP tools,
installs agent guidance, and shows durable task state in the sidebar. A
compatible MCP agent can then call `resume_session`, create djobs tasks before
multi-step edits, and finish each unit with evidence.

After that, keep talking normally: "continue", "fix this", "run tests", "retry",
"the previous run failed", or "release" are enough. The installed agent guidance
tells the AI to bring djobs in before editing; you do not need to mention djobs
in every prompt.

The setup step installs the runtime if needed, registers the MCP server,
installs the agent guidance block, and verifies everything with `djobs doctor`.
Use **djobs: Diagnose Setup** anytime to re-check.

## Compatibility status

This extension is implemented and tested with **GitHub Copilot in VS Code**.
djobs is MCP-based and is intended to work with Codex, Claude, Gemini, Cursor,
Cline, and other MCP-capable coding-agent hosts, but those non-Copilot flows are
not yet fully end-to-end tested.

## Upgrading from an older version

Version 0.8.5 removes the older prompt-driving commands and auto-takeover
settings. After upgrade, the extension only manages setup, MCP registration,
diagnostics, and the task sidebar. It also checks the VS Code Marketplace at
most once per day and tells you when a newer djobs extension version is
available, because VS Code auto-update can lag or be disabled.

Version 0.8.6 adds visible **Enable Prompt Actions** and **Disable Prompt
Actions** commands to the sidebar toolbar and Command Palette.

Prompt actions are available only if you explicitly enable
`djobs.promptActions.enabled`. When enabled, the sidebar shows **Prompt Agent to
Finish Workflow**; when disabled (the default), djobs never opens Chat or asks
whether to enable prompt actions. Use the sidebar toolbar command **Enable
Prompt Actions** (play-circle icon) or the Command Palette command **djobs:
Enable Prompt Actions** to turn it on for the current workspace.

## What the sidebar shows

- djobs tasks grouped by workflow and status.
- Stale (long-unfinished) and blocked tasks flagged, with one-click archive.
- Right-click a task to archive it, permanently delete it, view its audit
   history, copy its ID, or inspect its raw JSON.
- Skip or archive stale work without asking an agent to do it. Archive keeps the
   audit trail; delete removes the task and its events.
- When the current workspace has no active tasks, the empty state explains that
   tasks appear when an MCP-enabled agent records them.

## Advanced

The extension drives the `djobs` CLI under the hood. If you prefer to manage the
runtime yourself, install it once globally (`uv tool install djobs` or
`pipx install djobs` with Python 3.11+) and run `djobs init` in a project; the
extension will detect and use it. If djobs lives in a non-default interpreter,
set `djobs.pythonPath` in VS Code settings.
