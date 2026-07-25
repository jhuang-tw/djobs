# Examples

The primary djobs product is **local repository memory for AI coding agents**.

Start with:

```bash
python examples/memory_walkthrough.py
```

The older `run_*_demo.py` files exercise the original durable queue, worker pool, scheduler, and AI
task-platform prototypes. They remain temporarily for compatibility and regression reference, but
they are **not** the recommended onboarding path and will move under a dedicated legacy package in a
future compatibility cleanup.

For real host setup, use:

```bash
djobs setup
djobs doctor
djobs memory list
```

See `docs/USER_GUIDE.md` for storage, lifecycle, deletion, and troubleshooting.
