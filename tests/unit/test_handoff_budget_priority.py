from __future__ import annotations

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
