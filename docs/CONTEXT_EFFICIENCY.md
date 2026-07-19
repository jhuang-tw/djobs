# Context-efficient durable workflows

`djobs` stores exact workflow state while exposing a compact, budgeted view to
coding agents. The context-efficient MCP server is now the default for the
installed `djobs-mcp` command and for `djobs mcp`.

The equivalent direct module entry point is:

```bash
python -m djobs.low_token_mcp
```

The legacy `python -m djobs.mcp_server` entry point remains available for
compatibility, but it does not register the batch and capsule tools.

The default server adds three tools:

| Tool | Purpose |
|---|---|
| `enqueue_batch` | Create up to 200 tasks in one model/tool round trip. |
| `complete_batch` | Complete up to 200 tasks in one model/tool round trip. |
| `resume_capsule` | Return only the next useful tasks under an explicit context budget. |

## Recommended workflow

For a long multi-file operation, checkpoint in meaningful batches rather than
making two queue calls per file. Pass native arrays directly; JSON array strings
remain accepted for backward compatibility.

```text
enqueue_batch([...20 task specs...])
edit and verify a meaningful batch
complete_batch([...completed ids + concise evidence...])
```

After an interruption:

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

Queue counts and task IDs are exact. Token counts produced by
`resume_capsule` are estimates, not provider metering. Real savings claims must
compare host/provider usage from matched A/B runs, including:

- input tokens;
- cached-input tokens;
- output/reasoning tokens;
- MCP tool-call inputs and outputs;
- repeated file reads and edits;
- task completion quality.

This avoids assuming a fixed replay cost per task and presenting the resulting
counterfactual as observed savings.

## Headroom design reference

The implementation adopts three general ideas from Headroom's public design:

1. set an explicit context budget;
2. route only the most useful content into that budget;
3. preserve the original for retrieval on demand.

Headroom applies these ideas to arbitrary tool outputs, logs, code, and message
history. `djobs` applies an independent, dependency-free implementation only to
durable queue state. No Headroom source code is copied and no Headroom runtime
or ML dependency is added.

Reference: `headroomlabs-ai/headroom` (formerly published under
`chopratejas/headroom`).
