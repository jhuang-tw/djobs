"""Tests for low-token batch checkpointing and resume capsules."""

from __future__ import annotations

import json

import pytest

from djobs.low_token_mcp import complete_batch, enqueue_batch, resume_capsule
from djobs.mcp_server import configure, resume_session


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    configure(str(tmp_path / "low-token.db"))


def _task_specs(count: int) -> list[dict[str, object]]:
    return [
        {
            "type": "edit-file",
            "payload": {
                "file": f"src/file_{index}.py",
                "summary": f"Update file {index}",
                "internal_noise": {"large": "x" * 400},
            },
            "idempotency_key": f"edit:{index}",
        }
        for index in range(count)
    ]


def test_enqueue_batch_accepts_native_array_and_returns_compact_summaries():
    result = json.loads(enqueue_batch(_task_specs(20), correlation_id="compact-ws"))
    assert result["accepted_count"] == 20
    assert len(result["tasks"]) == 20
    assert set(result["tasks"][0]) == {"id", "type", "label"}


def test_enqueue_batch_keeps_json_string_compatibility():
    result = json.loads(enqueue_batch(json.dumps(_task_specs(2)), correlation_id="compat-ws"))
    assert result["accepted_count"] == 2


def test_resume_capsule_is_budgeted_and_recoverable():
    enqueue_batch(_task_specs(20), correlation_id="compact-ws")
    full = resume_session("compact-ws")
    capsule = json.loads(resume_capsule("compact-ws", max_items=3, token_budget=420))

    assert capsule["mode"] == "resume_capsule"
    assert capsule["counts"]["incomplete"] == 20
    assert 1 <= capsule["page"]["returned"] <= 3
    assert capsule["page"]["next_offset"] is not None
    assert capsule["recoverable"] is True
    assert capsule["budget"]["metered"] is False
    assert capsule["budget"]["estimated_tokens"] <= 420
    assert len(json.dumps(capsule, separators=(",", ":"))) < len(full)
    assert "internal_noise" not in capsule["tasks"][0].get("payload", {})
    assert capsule["tasks"][0].get("view_truncated") is True


def test_resume_capsule_paginates_without_repeating_tasks():
    enqueue_batch(_task_specs(8), correlation_id="compact-ws")
    first = json.loads(resume_capsule("compact-ws", max_items=2, token_budget=600))
    second = json.loads(
        resume_capsule(
            "compact-ws",
            max_items=2,
            token_budget=600,
            offset=first["page"]["next_offset"],
        )
    )
    assert {task["id"] for task in first["tasks"]}.isdisjoint(
        {task["id"] for task in second["tasks"]}
    )


def test_tiny_budget_does_not_force_an_oversized_task():
    enqueue_batch(_task_specs(1), correlation_id="tiny-ws")
    capsule = json.loads(resume_capsule("tiny-ws", max_items=1, token_budget=128))

    assert capsule["page"]["returned"] == 0
    assert capsule["budget"]["exhausted"] is True


def test_complete_batch_accepts_native_array_and_closes_many_tasks():
    created = json.loads(enqueue_batch(_task_specs(6), correlation_id="compact-ws"))
    completions = [
        {"task_id": task["id"], "evidence": f"completed {task['label']}"}
        for task in created["tasks"]
    ]
    result = json.loads(complete_batch(completions))
    assert result == {"completed_count": 6, "failed_count": 0, "failures": []}
    assert json.loads(resume_session("compact-ws"))["incomplete_count"] == 0


def test_batch_validation_returns_machine_readable_error():
    result = json.loads(enqueue_batch('{"not":"an array"}', correlation_id="ws"))
    assert result["error"] == "invalid batch"
    assert "JSON array" in result["detail"]


def test_cjk_estimate_is_not_divided_by_four():
    from djobs.low_token_mcp import _estimate_tokens

    english = _estimate_tokens("abcd" * 20)
    cjk = _estimate_tokens("測試資料" * 20)
    assert cjk > english
