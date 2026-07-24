# djobs — Project Memory for Coding Agents

![djobs project memory](https://raw.githubusercontent.com/jhuang-tw/djobs/main/vscode-ext/media/banner.png)

**Open a new AI session and continue the repository instead of explaining it again.**

The extension gives GitHub Copilot and VS Code Agent local repository memory without adding a
sidebar, dashboard, polling loop, remote service, or cloud database.

## What it remembers

- the user's bounded, redacted intent and important constraints;
- successful and failed tool results;
- actual Git working-tree changes;
- a compact session capsule before context compaction or exit.

When the next request arrives, `sync_workspace(query=..., context_tier="resume")` searches
relevant memories and returns only the smallest continuation capsule by default. Supporting
evidence and audit identifiers remain available on demand.

## Zero-touch start

Install the extension and use Copilot normally. The first djobs MCP call creates
`~/.djobs/global.db` and silently installs the passive Copilot lifecycle adapter. There is no
per-project setup command.

**djobs: Set up / Repair djobs** remains a fallback when Python is missing, an old launch path
needs repair, or diagnostics find a damaged installation.

## Five compact tools

- `sync_workspace(query?, context_tier?, ...)` recovers layered resume, evidence, or audit context.
- `memory(...)` lists, searches, forgets, or explicitly clears passive repository memory.
- `checkpoint(...)` deliberately creates or resumes one tracked task and claims its lease.
- `handoff(...)` explicitly releases or completes tracked work with bounded evidence.
- `resume_delta(...)` preserves compatibility for integrations already storing revision IDs.

Passive memory never creates or claims tasks. Lower-level queue and administration tools remain
available through `djobs-mcp-full`, not in every ordinary Agent context.

## Memory control

Ask Copilot naturally:

```text
What does djobs remember about the login bug?
Forget the memory about the abandoned Redis approach.
Clear djobs memory for this repository.
```

Put `[djobs:no-memory]` in a prompt to skip that prompt. Set
`DJOBS_CAPTURE_USER_INTENT=0` to disable automatic prompt-intent memory globally.

## Commands

- **djobs: Set up / Repair djobs** — install or update the engine, hooks, and native MCP registration.
- **djobs: Diagnose Setup** — verify runtime, MCP, local database, and hook health.
- **djobs: Pause djobs** — temporarily disable djobs operations without deleting state.
- **djobs: Resume djobs** — re-enable djobs.

## Privacy

State stays on the user's machine. Common credentials are redacted on a best-effort basis, hook
failures are fail-open, unrelated settings are preserved, and no memory or task state is uploaded
by djobs.
