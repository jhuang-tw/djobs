"""Tests for explainable djobs token-savings analytics."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
        now=datetime.now(UTC),
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
    assert "provider billing data" in result["assumptions"]["note"]


def test_gain_rejects_invalid_estimation_parameters(tmp_path: Path) -> None:
    db = tmp_path / "gain.db"

    with pytest.raises(ValueError, match="greater than 0"):
        build_gain_report(db, None, chars_per_token=0)
