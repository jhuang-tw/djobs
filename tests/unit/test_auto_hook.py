"""Tests for deterministic command rewriting and automatic checkpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from djobs import auto_hook
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository


def _payload(command: str, cwd: Path, *, tool_name: str = "Bash") -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "session-1",
        "timestamp": "2026-07-22T00:00:00Z",
        "cwd": str(cwd),
        "tool_name": tool_name,
        "tool_input": {"command": command, "timeout": 120},
    }


def test_smart_mode_rewrites_meaningful_command(tmp_path: Path) -> None:
    result = auto_hook.rewrite_pre_tool_payload(_payload("pytest -q", tmp_path), mode="smart")

    assert result["permissionDecision"] == "allow"
    modified = result["modifiedArgs"]
    assert modified["command"].startswith("djobs hook run --payload ")
    assert modified["timeout"] == 120


def test_smart_mode_skips_read_only_command(tmp_path: Path) -> None:
    assert auto_hook.rewrite_pre_tool_payload(_payload("git status", tmp_path), mode="smart") == {}


def test_all_mode_rewrites_read_only_command(tmp_path: Path) -> None:
    result = auto_hook.rewrite_pre_tool_payload(_payload("git status", tmp_path), mode="all")
    assert result["modifiedArgs"]["command"].startswith("djobs hook run --payload ")


def test_state_only_command_is_never_rewritten(tmp_path: Path) -> None:
    assert auto_hook.rewrite_pre_tool_payload(_payload("cd src", tmp_path), mode="all") == {}


def test_camel_case_payload_is_supported(tmp_path: Path) -> None:
    payload = {
        "sessionId": "session-2",
        "timestamp": 123,
        "cwd": str(tmp_path),
        "toolName": "bash",
        "toolArgs": json.dumps({"command": "npm run build"}),
    }
    result = auto_hook.rewrite_pre_tool_payload(payload, mode="smart")
    assert result["modifiedArgs"]["command"].startswith("djobs hook run --payload ")


def test_wrapped_success_records_completed_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auto_hook, "_execute_command", lambda *_args: 0)
    payload = {
        "command": "pytest -q",
        "shell": "bash",
        "cwd": str(tmp_path),
        "session_id": "session-3",
    }

    assert auto_hook.run_wrapped_payload(payload) == 0

    repo = SQLiteJobRepository.from_path(tmp_path / "djobs_mcp.db")
    jobs = repo.list_jobs_by_correlation_ids([str(tmp_path)])
    assert len(jobs) == 1
    assert jobs[0].type == "auto-command"
    assert jobs[0].status.value == "succeeded"
    assert jobs[0].payload["summary"] == "pytest -q"


def test_wrapped_failure_records_failed_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auto_hook, "_execute_command", lambda *_args: 7)
    payload = {
        "command": "npm test",
        "shell": "bash",
        "cwd": str(tmp_path),
        "session_id": "session-4",
    }

    assert auto_hook.run_wrapped_payload(payload) == 7

    repo = SQLiteJobRepository.from_path(tmp_path / "djobs_mcp.db")
    jobs = repo.list_jobs_by_correlation_ids([str(tmp_path)])
    assert jobs[0].status.value == "failed"
    assert jobs[0].last_error == "automatic command checkpoint: exit 7"


def test_session_start_injects_unfinished_checkpoint(tmp_path: Path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "djobs_mcp.db")
    queue = QueueService(repo)
    queue.submit(
        "auto-command",
        {"summary": "npm run build"},
        correlation_id=str(tmp_path),
    )

    result = auto_hook.session_start_context({"cwd": str(tmp_path)})

    assert "additionalContext" in result
    assert "1 unfinished checkpoint" in result["additionalContext"]
    assert "npm run build" in result["additionalContext"]


def test_install_hooks_is_idempotent(tmp_path: Path) -> None:
    target = auto_hook.install_hooks(tmp_path, mode="smart")
    first = target.read_text(encoding="utf-8")
    target_again = auto_hook.install_hooks(tmp_path, mode="smart")

    assert target_again == target
    assert target.read_text(encoding="utf-8") == first
    config = json.loads(first)
    assert "SessionStart" in config["hooks"]
    assert "PreToolUse" in config["hooks"]
    assert config["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert "djobs hook pre" in first


def test_hook_doctor_reports_missing_and_installed(tmp_path: Path) -> None:
    ok, _ = auto_hook.hook_doctor(tmp_path)
    assert not ok

    auto_hook.install_hooks(tmp_path)
    ok, detail = auto_hook.hook_doctor(tmp_path)
    assert ok
    assert "installed at" in detail
