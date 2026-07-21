"""Tests for revision-based delta context recovery."""

from __future__ import annotations

import json

import pytest
from djobs.delta_mcp import resume_delta
from djobs.low_token_mcp import complete_batch, enqueue_batch
from djobs.mcp_server import configure


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    db_path = tmp_path / "delta-context.db"
    configure(str(db_path))
    return db_path


def _task_specs(count: int) -> list[dict[str, object]]:
    return [
        {
            "type": "edit-file",
            "payload": {
                "file": f"src/file_{index}.py",
                "summary": f"Update file {index}",
                "internal_noise": {"large": "x" * 400},
            },
            "idempotency_key": f"delta:{index}",
        }
        for index in range(count)
    ]


def test_initial_delta_returns_added_tasks_revision_and_state_hash():
    enqueue_batch(_task_specs(3), correlation_id="delta-ws")

    delta = json.loads(resume_delta("delta-ws", max_items=5, token_budget=1000))

    assert delta["mode"] == "resume_delta"
    assert delta["revision"] == delta["head_revision"]
    assert delta["revision"] > 0
    assert delta["has_more"] is False
    assert delta["snapshot_consistent"] is True
    assert len(delta["state_hash"]) == 64
    assert [item["change"] for item in delta["changes"]] == ["added"] * 3
    assert all("internal_noise" not in item.get("payload", {}) for item in delta["changes"])
    assert delta["budget"]["estimated_tokens"] <= 1000


def test_matching_revision_and_hash_returns_no_repeated_tasks():
    enqueue_batch(_task_specs(2), correlation_id="delta-ws")
    first = json.loads(resume_delta("delta-ws", token_budget=1000))

    second = json.loads(
        resume_delta(
            "delta-ws",
            since_revision=first["revision"],
            known_state_hash=first["state_hash"],
            token_budget=1000,
        )
    )

    assert second["unchanged"] is True
    assert second["changes"] == []
    assert second["next"] == []
    assert second["revision"] == first["revision"]
    assert second["state_hash"] == first["state_hash"]


def test_completion_is_reported_as_delta_and_changes_state_hash():
    created = json.loads(enqueue_batch(_task_specs(2), correlation_id="delta-ws"))
    first = json.loads(resume_delta("delta-ws", token_budget=1000))
    task_id = created["tasks"][0]["id"]

    complete_batch([{"task_id": task_id, "evidence": "updated and tested"}])
    second = json.loads(
        resume_delta(
            "delta-ws",
            since_revision=first["revision"],
            known_state_hash=first["state_hash"],
            token_budget=1000,
        )
    )

    assert second["changes"][0]["id"] == task_id
    assert second["changes"][0]["change"] == "completed"
    assert second["changes"][0]["last_event"] == "job_succeeded"
    assert second["state_hash"] != first["state_hash"]
    assert second["counts"]["incomplete"] == 1


def test_delta_cursor_pages_without_skipping_changed_tasks():
    enqueue_batch(_task_specs(4), correlation_id="delta-ws")

    first = json.loads(resume_delta("delta-ws", max_items=1, token_budget=1000))
    second = json.loads(
        resume_delta(
            "delta-ws",
            since_revision=first["revision"],
            max_items=1,
            token_budget=1000,
        )
    )

    assert first["has_more"] is True
    assert first["revision"] < first["head_revision"]
    assert len(first["changes"]) == 1
    assert len(second["changes"]) == 1
    assert first["changes"][0]["id"] != second["changes"][0]["id"]
    assert second["revision"] > first["revision"]


def test_permanent_deletion_emits_tombstone_without_reusing_revision(_fresh_db):
    created = json.loads(enqueue_batch(_task_specs(1), correlation_id="delta-ws"))
    first = json.loads(resume_delta("delta-ws", token_budget=1000))
    task_id = created["tasks"][0]["id"]

    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(_fresh_db)
    with repo._lock:
        repo._connection.execute("DELETE FROM job_events WHERE job_id = ?", (task_id,))
        repo._connection.execute("DELETE FROM jobs WHERE id = ?", (task_id,))
        repo._connection.commit()

    deleted = json.loads(
        resume_delta("delta-ws", since_revision=first["revision"], token_budget=1000)
    )
    assert deleted["changes"][0]["id"] == task_id
    assert deleted["changes"][0]["change"] == "deleted"
    assert deleted["changes"][0]["status"] == "deleted"
    assert deleted["revision"] > first["revision"]

    enqueue_batch(
        [
            {
                "type": "edit-file",
                "payload": {"file": "src/new_file.py", "summary": "New task"},
                "idempotency_key": "delta:new",
            }
        ],
        correlation_id="delta-ws",
    )
    added = json.loads(
        resume_delta("delta-ws", since_revision=deleted["revision"], token_budget=1000)
    )
    assert added["changes"][0]["change"] == "added"
    assert added["revision"] > deleted["revision"]


def test_cursor_from_another_database_requests_reset():
    enqueue_batch(_task_specs(2), correlation_id="delta-ws")

    delta = json.loads(
        resume_delta(
            "delta-ws",
            since_revision=999_999,
            max_items=2,
            token_budget=1000,
        )
    )

    assert delta["reset_required"] is True
    assert delta["delta_from"] == 999_999
    assert delta["revision"] <= delta["head_revision"]
    assert delta["changes"]


def test_invalid_revision_is_machine_readable():
    result = json.loads(resume_delta("delta-ws", since_revision=-1))

    assert result == {"error": "since_revision must be a non-negative integer"}


def test_tiny_budget_returns_compact_exhausted_cursor():
    enqueue_batch(_task_specs(2), correlation_id="delta-ws")

    delta = json.loads(resume_delta("delta-ws", max_items=2, token_budget=128))

    assert delta["revision"] == 0
    assert delta["has_more"] is True
    assert delta["budget"]["exhausted"] is True
    assert delta["budget"]["estimated_tokens"] <= 128
    assert "changes" not in delta


def test_interleaved_pages_return_state_at_each_revision():
    created = json.loads(enqueue_batch(_task_specs(2), correlation_id="delta-ws"))
    first_id = created["tasks"][0]["id"]
    complete_batch([{"task_id": first_id, "evidence": "done"}])

    first = json.loads(resume_delta("delta-ws", max_items=1, token_budget=1000))
    second = json.loads(
        resume_delta(
            "delta-ws",
            since_revision=first["revision"],
            max_items=1,
            token_budget=1000,
        )
    )
    third = json.loads(
        resume_delta(
            "delta-ws",
            since_revision=second["revision"],
            max_items=1,
            token_budget=1000,
        )
    )

    assert first["changes"][0]["id"] == first_id
    assert first["changes"][0]["status"] == "pending"
    assert first["changes"][0]["change"] == "added"
    assert second["changes"][0]["id"] != first_id
    assert second["changes"][0]["status"] == "pending"
    assert third["changes"][0]["id"] == first_id
    assert third["changes"][0]["status"] == "succeeded"
    assert third["changes"][0]["change"] == "completed"
