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
3. Start a new agent session - it will resume unfinished work automatically.
   For new multi-step work, run **djobs: Start Tracked Workflow** so the agent
   resumes first, then creates djobs tasks before editing. The command copies a
   prompt first; it does not spend tokens unless you open or paste it into Chat.

When djobs is ready, the extension asks once per workspace whether it may
auto-take over future AI work. Choosing **Allow auto takeover** only changes the
workspace setting; it does not open Chat or spend tokens immediately. Later
sessions open Chat with the right resume/enqueue prompt before multi-step edits.
It cannot silently intercept every AI chat message; instead it wires MCP tools,
installs agent guidance, and brings Chat to the correct starting prompt.

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

After upgrade, djobs asks once per workspace whether it may automatically open
Chat with the right resume/enqueue prompt in future sessions. Choose **Allow
auto takeover** for the most hands-off flow (no tokens are spent immediately),
**Ask each time** if you want a prompt before Chat opens, or turn it off later
with `djobs.autoTakeoverMode`.

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
