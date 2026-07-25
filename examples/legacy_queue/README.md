# Legacy durable queue examples

These examples exercise the original djobs queue, worker, scheduler, dashboard-era MCP tools, and
AI task-platform prototypes.

They are retained for compatibility testing and for users who already depend on the durable queue
engine. They are **not** the recommended entry point for the current djobs product. The
`dead_letter_example.py` script also lives here because it operates directly on queue state.

For local AI coding-agent memory, start here instead:

```bash
python examples/memory_walkthrough.py
djobs setup
djobs doctor
djobs memory list
```

The corresponding operational CLI is intentionally separated under:

```bash
djobs legacy --help
```
