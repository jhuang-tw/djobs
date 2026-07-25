# Examples

The primary djobs product is **local repository memory for AI coding agents**.

Start with the root-level product walkthrough:

```bash
python examples/memory_walkthrough.py
```

The original durable queue, worker pool, scheduler, and AI task-platform demos have been moved to
[`examples/legacy_queue/`](legacy_queue/). They remain available for compatibility and regression
reference, but no longer compete with the memory product in the first directory view.

For real host setup, use:

```bash
djobs setup
djobs doctor
djobs memory list
```

See `docs/USER_GUIDE.md` for storage, lifecycle, deletion, and troubleshooting.
