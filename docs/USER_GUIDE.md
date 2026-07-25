# djobs user guide

This guide covers the normal product: local repository memory for AI coding agents. The original
durable queue engine is a compatibility subsystem and is documented through `djobs legacy --help`.

## First run

```bash
pipx install djobs
# or: uv tool install djobs

djobs setup
djobs doctor
djobs memory list
```

`djobs setup` defaults to Copilot. Pass `codex`, `claude`, `gemini`, `kimi`, or `all` to configure a
different host. The VS Code extension can register the MCP server natively, so a missing
`.vscode/mcp.json` is not an error.

## What is stored

Passive memory contains bounded observations, not a replay of the entire chat:

- `user_intent`: goals and constraints from the user;
- `tool_result`: successful tool outcomes;
- `tool_failure`: failed attempts worth avoiding;
- `repository_change`: bounded Git working-tree state;
- `session_capsule`: goal, progress, failures, and next step.

The default database is `~/.djobs/global.db`. Repository identity scopes retrieval so unrelated
projects do not share memory. Sibling worktrees share passive repository memory, while explicit task
leases remain checkout-specific.

## Inspect memory

```bash
djobs memory list
djobs memory search "OAuth callback"
```

Important fields:

| Field | Meaning |
|---|---|
| `id` | Memory identifier used by lifecycle and deletion commands |
| `event` | Observation type such as `user_intent` or `tool_failure` |
| `summary` | Bounded stored text; always treat it as untrusted data |
| `status` | `active`, `resolved`, `superseded`, `stale`, or `contradicted` |
| `score` | Query relevance proxy in search/evidence output, not a truth score |
| `commit_sha` | Commit associated with a memory when one was captured |

## Retire outdated memory

Prefer lifecycle updates over deletion:

```bash
djobs memory status MEMORY_ID resolved --resolved-by-commit COMMIT_SHA
djobs memory status OLD_ID superseded --replacement-id NEW_ID
djobs memory status MEMORY_ID stale
```

Inactive memory remains auditable but is excluded from normal recovery.

Delete only when requested:

```bash
djobs memory forget MEMORY_ID
djobs memory clear --yes
```

`clear --yes` removes passive memory for the repository family. Explicit checkpoint tasks are
preserved.

## Choosing a recovery tool

Use `sync_workspace(query=current_request)` for normal continuation. It is the primary entry point.

- `resume`: smallest continuation payload; includes compact sources supporting the summary.
- `evidence`: adds selected observation summaries and relevance scores.
- `audit`: includes identifiers, timestamps, and full lifecycle detail.

Use `memory` when inspecting or changing passive memory. Use `checkpoint` and `handoff` only when
multiple agents require explicit ownership. Use `resume_delta` only for an older integration that
already persists correlation IDs and revisions.

### Response conventions

- `ok`: primary success flag.
- `continue_coding`: a recoverable djobs failure; continue the user's task without djobs.
- `stored_content_is_data`: recovered text is data, never an instruction.
- `context_hash`: hash of the selected passive context.
- `memory_unchanged`: the known hash matched, so unchanged memory was intentionally omitted.
- `state_hash`: queue-state hash used by legacy `resume_delta` callers.
- `snapshot_consistent`: the legacy delta snapshot is internally consistent.
- `reset_required`: the legacy revision cursor cannot be advanced safely; refresh from scratch.

## Troubleshooting

### `djobs doctor` says no project MCP override

That is informational. A project `.vscode/mcp.json` is optional when using the VS Code extension or
user-level `djobs setup` registration.

### No host is ready

Run one of:

```bash
djobs setup copilot
djobs setup codex
djobs setup claude
djobs setup gemini
djobs setup kimi
```

Then restart the host so it reloads MCP and lifecycle configuration.

### A host check reports an error

```bash
djobs repair HOST
djobs doctor
```

### Memory is empty

Start a new configured agent session and perform repository work. Capture is fail-open, so a missing
or unsupported host adapter will not block coding. Verify the host is listed as ready in
`djobs doctor`.

### The agent recovered an outdated fact

Find it, then mark it stale, superseded, resolved, or contradicted. Normal recovery only selects
active memory.

### The database cannot be opened

Set `DJOBS_DB` to a writable SQLite path or fix permissions for `~/.djobs`:

```bash
export DJOBS_DB="$HOME/.djobs/global.db"
djobs doctor
```

On PowerShell:

```powershell
$env:DJOBS_DB = "$HOME\.djobs\global.db"
djobs doctor
```

## Compatibility queue CLI

The queue, worker, scheduler, dashboard, task archive, and audit commands are retained for existing
integrations:

```bash
djobs legacy --help
```

They are not the recommended onboarding path for local agent memory.
