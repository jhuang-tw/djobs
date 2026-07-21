# Context-efficient durable workflows

`djobs` stores exact workflow state while exposing compact, budgeted views to
coding agents. The default installed `djobs-mcp` server now provides both
budgeted capsules and revision-based delta recovery.

The equivalent direct module entry point is:

```bash
python -m djobs.delta_mcp
```

The legacy entry points remain available:

```bash
python -m djobs.low_token_mcp  # batch + capsule tools
python -m djobs.mcp_server     # core tools only
```

## Context-efficient tools

| Tool | Purpose |
|---|---|
| `enqueue_batch` | Create up to 200 tasks in one model/tool round trip. |
| `complete_batch` | Complete up to 200 tasks in one model/tool round trip. |
| `resume_capsule` | Return the next useful tasks under an explicit context budget. |
| `resume_delta` | Return only task state changed since a stable event revision. |

## Recommended workflow

For a long multi-file operation, checkpoint in meaningful batches rather than
making two queue calls per file. Pass native arrays directly; JSON array strings
remain accepted for backward compatibility.

```text
enqueue_batch([...20 task specs...])
edit and verify a meaningful batch
complete_batch([...completed ids + concise evidence...])
```

After the first interruption or handoff, request an initial delta:

```text
resume_delta(correlation_id, since_revision=0, max_items=5, token_budget=600)
```

When `has_more=true`, call again with the returned `revision`. Once
`snapshot_consistent=true`, persist that `revision` and `state_hash` together in
the agent's compact working state. On the next handoff, send them back:

```text
resume_delta(
    correlation_id,
    since_revision=<last complete revision>,
    known_state_hash=<matching state hash>,
    max_items=5,
    token_budget=600,
)
```

The delta response:

- uses an append-only SQLite revision ledger with non-reusable integer cursors;
- stores task state at each event revision so pagination cannot reveal future state early;
- reports added, updated, completed, failed, and permanently deleted task state;
- returns `has_more=true` when the caller should continue from the returned revision;
- includes a deterministic SHA-256 hash of all active task state;
- returns no repeated tasks when both revision and state hash are unchanged;
- resets safely when a revision came from a different or recreated database;
- keeps exact task records retrievable through `check_task` and `resume_session`.

`resume_delta` is currently SQLite-only because it reads the SQLite
`context_revisions` ledger. PostgreSQL continues to use the normal core and
capsule tools until its MCP path exposes the same compact delta query.

For callers that do not persist a revision, `resume_capsule` remains the simple
stateless recovery path:

```text
resume_capsule(correlation_id, max_items=5, token_budget=600)
```

The capsule:

- ranks ready tasks before blocked tasks;
- strips timestamps, attempt counters, correlation IDs, leases, and unrelated
  payload fields from the model-facing response;
- paginates with `next_offset`;
- reports a heuristic context estimate with `metered: false`;
- returns no task rather than exceeding a very small requested budget;
- keeps every exact task record in SQLite.

Use `check_task(task_id)` for one full record or `resume_session(correlation_id)`
when the complete unfinished set is genuinely needed.

## Measurement rules

Queue counts, task IDs, revisions, and state hashes are exact. Token counts
produced by the context-efficient tools are estimates, not provider metering.
Real savings claims must compare host/provider usage from matched A/B runs,
including:

- input tokens;
- cached-input tokens;
- output/reasoning tokens;
- MCP tool-call inputs and outputs;
- repeated file reads and edits;
- task completion quality.

This avoids assuming a fixed replay cost per task and presenting the resulting
counterfactual as observed savings.

## Design reference

The implementation follows three general context-management principles:

1. set an explicit context budget;
2. route only the most useful content into that budget;
3. preserve the original for retrieval on demand.

`resume_delta` adds a fourth principle: after the initial snapshot, transmit
only state changes and a verification hash instead of replaying the same task
list. The implementation is dependency-free and operates only on durable queue
state; it does not compress arbitrary model messages or source files.
