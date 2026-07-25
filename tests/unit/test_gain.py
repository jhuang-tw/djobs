"""Tests for explainable djobs token-savings analytics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from djobs.gain import build_gain_report, main
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository


def _queue(path: Path) -> tuple[SQLiteJobRepository, QueueService]:
    repo = SQLiteJobRepository.from_path(path)
    return repo, QueueService(repo)


def test_gain_splits_automatic_and_workflow_savings(tmp_path: Path) -> None:
    db = tmp_path / "gain.db"
    correlation_id = str(tmp_path)
    repo, queue = _queue(db)

    workflow = queue.submit(
        "edit-file",
        {"summary": "Refactor the parser", "file": "src/parser.py"},
        correlation_id=correlation_id,
    )
    queue.complete(workflow.id, evidence="Updated parser and tests")

    automatic = queue.submit(
        "auto-command",
        {
            "summary": "pytest -q tests/unit/test_parser.py",
            "command": "pytest -q tests/unit/test_parser.py",
            "source": "preToolUse",
        },
        correlation_id=correlation_id,
    )
    queue.complete(automatic.id, evidence="automatic command checkpoint: exit 0")
    queue.archive(automatic.id, "Automatic command completed")

    failed = queue.submit(
        "auto-command",
        {"summary": "npm test", "command": "npm test", "source": "preToolUse"},
        correlation_id=correlation_id,
    )
    queue.fail(failed.id, "automatic command checkpoint: exit 1")

    report = build_gain_report(
        db,
        correlation_id,
        now=datetime.now(timezone.utc),
    )

    totals = report["all_time"]
    assert totals["completed_records"] == 2
    assert totals["estimated_saved_tokens"] > 0
    assert totals["sources"]["automatic_hook"]["completed_records"] == 1
    assert totals["sources"]["durable_workflow"]["completed_records"] == 1
    assert report["recoverable"]["checkpoints"] == 1
    assert report["recoverable"]["by_status"] == {"failed": 1}

    automatic_history = next(
        item for item in report["history"] if item["source"] == "automatic_hook"
    )
    assert automatic_history["label"] == "npm test" or automatic_history["label"].startswith(
        "pytest -q tests/unit/test_parser.py"
    )
    assert repo.get_job(automatic.id) is not None


def test_gain_reports_verified_task_efficiency_and_repairs(tmp_path: Path) -> None:
    db = tmp_path / "gain.db"
    _, queue = _queue(db)
    correlation_id = "workspace-efficiency"

    first_pass = queue.submit(
        "coding-checkpoint",
        {"summary": "Implement parser validation"},
        correlation_id=correlation_id,
        max_attempts=2,
    )
    claimed = queue.claim("worker-one")
    assert claimed is not None and claimed.id == first_pass.id
    queue.complete(first_pass.id, evidence="focused validation passed")

    repaired = queue.submit(
        "coding-checkpoint",
        {"summary": "Repair integration binding"},
        correlation_id=correlation_id,
        max_attempts=3,
    )
    claimed = queue.claim("worker-two")
    assert claimed is not None and claimed.id == repaired.id
    now = datetime.now(timezone.utc)
    queue.retry_or_dead_letter(repaired.id, "first validation failed", now=now)
    queue.promote_due_retries(now=now + timedelta(hours=1))
    claimed_again = queue.claim("worker-two")
    assert claimed_again is not None and claimed_again.id == repaired.id
    queue.complete(repaired.id, evidence="repair passed validation")

    report = build_gain_report(db, correlation_id, now=now + timedelta(hours=1))
    efficiency = report["all_time"]["verified_task_efficiency"]

    assert efficiency["verified_tasks"] == 2
    assert efficiency["first_pass_verified_tasks"] == 1
    assert efficiency["first_pass_verified_percent"] == 50.0
    assert efficiency["repair_attempts"] == 1
    assert efficiency["average_attempts_per_verified_task"] == 1.5
    assert efficiency["cost_per_verified_task"]["estimated_context_tokens"] > 0
    assert efficiency["cost_per_verified_task"]["average_cycle_seconds"] >= 0


def test_gain_filters_by_workspace(tmp_path: Path) -> None:
    db = tmp_path / "gain.db"
    _, queue = _queue(db)
    first = queue.submit(
        "task-a",
        {"summary": "First workspace"},
        correlation_id="workspace-a",
    )
    second = queue.submit(
        "task-b",
        {"summary": "Second workspace"},
        correlation_id="workspace-b",
    )
    queue.complete(first.id, evidence="done a")
    queue.complete(second.id, evidence="done b")

    report = build_gain_report(db, "workspace-a")

    assert report["all_time"]["completed_records"] == 1
    assert all(item["correlation_id"] == "workspace-a" for item in report["history"])


def test_gain_all_scope_aggregates_workspaces(tmp_path: Path) -> None:
    db = tmp_path / "gain.db"
    _, queue = _queue(db)
    for workspace in ("workspace-a", "workspace-b"):
        job = queue.submit(
            "task",
            {"summary": workspace},
            correlation_id=workspace,
        )
        queue.complete(job.id, evidence=f"completed {workspace}")

    report = build_gain_report(db, None)

    assert report["scope"] == "all workspaces"
    assert report["all_time"]["completed_records"] == 2
    assert report["all_time"]["sources"]["durable_workflow"]["completed_records"] == 2


def test_gain_cli_json_defaults_to_current_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "gain.db"
    _, queue = _queue(db)
    job = queue.submit(
        "task",
        {"summary": "Current workspace task"},
        correlation_id=str(tmp_path),
    )
    queue.complete(job.id, evidence="done")
    monkeypatch.chdir(tmp_path)

    assert main(["--db", str(db), "--format", "json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == str(tmp_path)
    assert result["all_time"]["completed_records"] == 1
    assert "provider usage or billing data" in result["assumptions"]["note"]
    assert "Modern agents" in result["assumptions"]["note"]


def test_gain_rejects_invalid_estimation_parameters(tmp_path: Path) -> None:
    db = tmp_path / "gain.db"

    with pytest.raises(ValueError, match="greater than 0"):
        build_gain_report(db, None, chars_per_token=0)
