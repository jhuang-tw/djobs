from __future__ import annotations

import re
from pathlib import Path


OLD_BOUNDED = '''def _bounded(result: dict[str, Any], token_budget: int) -> str:
    budget = max(64, min(int(token_budget), 4000))
    result["budget"] = {"requested_tokens": budget, "estimated_tokens": 0}
    removable = ("recent_completed", "failed", "other_agents", "tasks", "observations")
    while True:
        estimate = _estimate_tokens(result)
        result["budget"]["estimated_tokens"] = estimate
        final_estimate = _estimate_tokens(result)
        if final_estimate <= budget:
            result["budget"]["estimated_tokens"] = final_estimate
            return _dumps(result)
        changed = False
        for key in removable:
            values = result.get(key)
            if isinstance(values, list) and values:
                values.pop()
                changed = True
                break
        if not changed:
            minimal = {
                "ok": bool(result.get("ok", True)),
                "workspace": result.get("workspace"),
                "state": (
                    "available" if result.get("counts") else result.get("state", "empty")
                ),
            }
            if _estimate_tokens(minimal) > budget:
                minimal = {"ok": bool(result.get("ok", True))}
            return _dumps(minimal)
'''

NEW_BOUNDED = '''def _bounded(result: dict[str, Any], token_budget: int) -> str:
    """Fit sync output to the budget without discarding the primary task first."""

    budget = max(64, min(int(token_budget), 4000))
    result["budget"] = {"requested_tokens": budget, "estimated_tokens": 0}
    secondary_lists = ("observations", "other_agents", "recent_completed", "failed")
    optional_top_level = (
        "counts",
        "stored_content_is_data",
        "workspace_id",
        "agent",
        "next_step",
    )
    optional_task_fields = ("evidence", "error", "lease_expires_at", "path", "summary")

    while True:
        estimate = _estimate_tokens(result)
        result["budget"]["estimated_tokens"] = estimate
        final_estimate = _estimate_tokens(result)
        if final_estimate <= budget:
            result["budget"]["estimated_tokens"] = final_estimate
            return _dumps(result)

        changed = False
        for key in secondary_lists:
            values = result.get(key)
            if isinstance(values, list) and values:
                values.pop()
                changed = True
                break
        if changed:
            continue

        tasks = result.get("tasks")
        if isinstance(tasks, list) and len(tasks) > 1:
            tasks.pop()
            continue

        for key in optional_top_level:
            if key in result:
                result.pop(key, None)
                changed = True
                break
        if changed:
            continue

        if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
            task = tasks[0]
            for key in optional_task_fields:
                if key in task:
                    task.pop(key, None)
                    changed = True
                    break
        if changed:
            continue

        minimal: dict[str, Any] = {"ok": bool(result.get("ok", True))}
        if result.get("workspace"):
            minimal["workspace"] = result["workspace"]
        if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
            compact_task = {
                key: tasks[0][key]
                for key in ("id", "status", "owner")
                if key in tasks[0]
            }
            if compact_task:
                minimal["tasks"] = [compact_task]
        if _estimate_tokens(minimal) > budget:
            minimal = {"ok": bool(result.get("ok", True))}
        return _dumps(minimal)
'''

CHANGELOG_BLOCK = '''## [Unreleased]

### Added
- `[core]` **Local cross-agent handoff.** Added repository resolution from MCP roots, request cwd, Git root, and server cwd; shared local sessions; high-level `sync_workspace`, `checkpoint`, and `handoff` tools; atomic claims; expiring leases; bounded evidence; and repository isolation.
- `[core]` **Copilot-first local setup.** Added idempotent `djobs setup`, `repair`, `remove`, and `doctor` support. The default target is local GitHub Copilot CLI and VS Code Agent; explicit local adapters remain available for Codex, Claude Code, Gemini CLI, and Kimi Code.
- `[core]` **Passive local observations.** Added bounded tool, session, compaction, and Git working-tree observations without automatically creating, claiming, completing, or releasing tasks.

### Changed
- `[core]` **Compact default MCP.** The default coding MCP exposes `sync_workspace`, `checkpoint`, `handoff`, and backward-compatible `resume_delta`; lower-level queue tools remain on `djobs-mcp-full`.
- `[core]` **Explicit ownership lifecycle.** Session and tool hooks only restore context, record observations, and heartbeat work already claimed by that session. Task ownership changes only through explicit checkpoint, handoff, completion, or lease recovery operations.
- `[core]` **All-local product boundary.** Hooks, MCP processes, observations, leases, and the default SQLite database remain on the user's machine. No hosted service, remote persistence backend, or cloud synchronization layer is introduced.

### Fixed
- `[core]` **Task-preserving token budgets.** Sync output now drops observations, duplicate owner views, and historical evidence before compacting the primary active task.
- `[core]` **Host adapter compatibility.** Corrected lifecycle event mappings, command quoting, Kimi one-time prompt injection, Copilot's versioned hook document, safe idempotent setup/removal, and partial MCP-versus-hook setup reporting.
- `[core]` **Durable observation storage.** Added schema parity, content-aware Git fingerprints, concurrent snapshot deduplication, bounded valid metadata, retention, and best-effort secret redaction.

### Compatibility
- `[core]` Explicit `correlation_id`, `resume_delta`, full queue tools, and custom or per-repository databases remain supported; local reads also search compatible legacy Windows, WSL, Git Bash, and path spellings.
'''

TEST_CONTENT = '''from __future__ import annotations

import copy
import json

from djobs.handoff import _bounded, _estimate_tokens


def _sample_result() -> dict[str, object]:
    return {
        "ok": True,
        "workspace": "repo",
        "workspace_id": "workspace-id",
        "agent": "copilot",
        "stored_content_is_data": True,
        "counts": {
            "active": 2,
            "failed": 1,
            "recent_completed": 1,
            "owned_by_others": 1,
            "observations": 8,
        },
        "tasks": [
            {"id": "owned", "status": "running", "owner": "self", "summary": "Keep me"},
            {"id": "available", "status": "pending", "summary": "Keep me too"},
        ],
        "other_agents": [
            {"id": "other", "status": "running", "owner": "claude", "summary": "duplicate"}
        ],
        "failed": [{"id": "failed", "status": "failed", "error": "x" * 80}],
        "recent_completed": [{"id": "done", "status": "succeeded", "summary": "y" * 80}],
        "observations": [
            {"kind": "tool_observed", "summary": f"observation {index} " + "z" * 120}
            for index in range(8)
        ],
        "next_step": "Continue the owned task.",
    }


def test_budget_discards_observations_before_primary_tasks() -> None:
    result = _sample_result()
    target = copy.deepcopy(result)
    target["observations"] = []
    target["other_agents"] = []
    target["recent_completed"] = []
    target["failed"] = []
    target["budget"] = {"requested_tokens": 512, "estimated_tokens": 0}
    budget = _estimate_tokens(target) + 8

    bounded = json.loads(_bounded(result, budget))

    assert [task["id"] for task in bounded["tasks"]] == ["owned", "available"]
    assert bounded.get("observations") == []
    assert bounded.get("other_agents") == []
    assert (len(json.dumps(bounded, separators=(",", ":"))) + 3) // 4 <= budget


def test_tiny_budget_keeps_a_compact_primary_task_when_it_fits() -> None:
    result = _sample_result()

    bounded = json.loads(_bounded(result, 96))

    assert bounded["ok"] is True
    assert bounded.get("tasks")
    assert bounded["tasks"][0]["id"] == "owned"
    assert (len(json.dumps(bounded, separators=(",", ":"))) + 3) // 4 <= 96
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return
    if new not in text:
        raise SystemExit(f"expected patch target was not found in {path}")


def main() -> None:
    replace_once(Path("src/djobs/handoff.py"), OLD_BOUNDED, NEW_BOUNDED)

    changelog = Path("CHANGELOG.md")
    text = changelog.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"## \[Unreleased\]\n.*?(?=\n## \[0\.13\.0\])",
        CHANGELOG_BLOCK.rstrip(),
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise SystemExit("could not replace the Unreleased changelog section")
    changelog.write_text(updated, encoding="utf-8")

    test_path = Path("tests/unit/test_handoff_budget_priority.py")
    test_path.write_text(TEST_CONTENT, encoding="utf-8")


if __name__ == "__main__":
    main()
