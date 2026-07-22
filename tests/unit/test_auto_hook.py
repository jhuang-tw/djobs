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


def _decoded_rewrite(result: dict[str, object]) -> dict[str, object]:
    modified = result["modifiedArgs"]
    assert isinstance(modified, dict)
    command = modified["command"]
    assert isinstance(command, str)
    encoded = command.removeprefix("djobs hook run --payload ")
    return auto_hook._decode_envelope(encoded)


def test_smart_mode_rewrites_meaningful_command_for_both_output_schemas(
    tmp_path: Path,
) -> None:
    result = auto_hook.rewrite_pre_tool_payload(
        _payload("pytest -q", tmp_path),
        mode="smart",
    )

    assert result["permissionDecision"] == "allow"
    modified = result["modifiedArgs"]
    assert modified["command"].startswith("djobs hook run --payload ")
    assert modified["timeout"] == 120

    specific = result["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "allow"
    assert specific["updatedInput"] == modified


def test_smart_mode_skips_read_only_command(tmp_path: Path) -> None:
    assert (
        auto_hook.rewrite_pre_tool_payload(
            _payload("git status", tmp_path),
            mode="smart",
        )
        == {}
    )


def test_all_mode_rewrites_read_only_command(tmp_path: Path) -> None:
    result = auto_hook.rewrite_pre_tool_payload(
        _payload("git status", tmp_path),
        mode="all",
    )
    assert result["modifiedArgs"]["command"].startswith("djobs hook run --payload ")


def test_state_only_command_is_never_rewritten(tmp_path: Path) -> None:
    assert (
        auto_hook.rewrite_pre_tool_payload(
            _payload("cd src", tmp_path),
            mode="all",
        )
        == {}
    )


def test_camel_case_payload_is_supported(tmp_path: Path) -> None:
    payload = {
        "sessionId": "session-2",
        "timestamp": 123,
        "cwd": str(tmp_path),
        "toolName": "bash",
        "toolArgs": json.dumps({"command": "npm run build"}),
    }
    result = auto_hook.rewrite_pre_tool_payload(payload, mode="smart")
    assert _decoded_rewrite(result)["shell"] == "bash"


def test_native_powershell_payload_preserves_powershell(tmp_path: Path) -> None:
    payload = {
        "sessionId": "session-ps",
        "timestamp": 123,
        "cwd": str(tmp_path),
        "toolName": "powershell",
        "toolArgs": {"command": "npm run build"},
    }
    result = auto_hook.rewrite_pre_tool_payload(payload, mode="smart")
    assert _decoded_rewrite(result)["shell"] == "powershell"


def test_vscode_terminal_uses_extension_host_platform(tmp_path: Path) -> None:
    payload = _payload("npm run build", tmp_path, tool_name="runTerminalCommand")
    assert auto_hook._shell_kind(payload, platform_name="posix") == "bash"
    assert auto_hook._shell_kind(payload, platform_name="nt") == "powershell"


def test_wrapped_success_archives_checkpoint_without_sidebar_noise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    assert jobs[0].status.value == "archived"
    assert jobs[0].payload["summary"] == "pytest -q"
    events = repo.list_events(jobs[0].id)
    assert any(event.event_type == "job_succeeded" for event in events)
    assert any(event.event_type == "job_archived" for event in events)


def test_wrapped_failure_records_failed_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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


def test_session_start_injects_unfinished_and_failed_checkpoints(tmp_path: Path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "djobs_mcp.db")
    queue = QueueService(repo)
    queue.submit(
        "auto-command",
        {"summary": "npm run build"},
        correlation_id=str(tmp_path),
    )
    failed = queue.submit(
        "auto-command",
        {"summary": "npm test"},
        correlation_id=str(tmp_path),
    )
    queue.fail(failed.id, "exit 1")

    result = auto_hook.session_start_context({"cwd": str(tmp_path)})

    assert "additionalContext" in result
    assert "2 unfinished checkpoint" in result["additionalContext"]
    assert "npm run build" in result["additionalContext"]
    assert "npm test" in result["additionalContext"]
    assert result["hookSpecificOutput"]["additionalContext"] == result["additionalContext"]


def test_install_hooks_is_idempotent_and_uses_native_event_names(tmp_path: Path) -> None:
    target = auto_hook.install_hooks(tmp_path, mode="smart")
    first = target.read_text(encoding="utf-8")
    target_again = auto_hook.install_hooks(tmp_path, mode="smart")

    assert target_again == target
    assert target.read_text(encoding="utf-8") == first
    config = json.loads(first)
    assert "sessionStart" in config["hooks"]
    assert "preToolUse" in config["hooks"]
    matcher = config["hooks"]["preToolUse"][0]["matcher"]
    assert "bash" in matcher
    assert "powershell" in matcher
    assert "djobs hook pre" in first


def test_install_hooks_propagates_shared_database(tmp_path: Path) -> None:
    shared = tmp_path / "shared" / "global.db"
    target = auto_hook.install_hooks(tmp_path, db_path=shared)
    config = json.loads(target.read_text(encoding="utf-8"))

    for event in ("sessionStart", "preToolUse"):
        environment = config["hooks"][event][0]["env"]
        assert environment["DJOBS_DB"] == str(shared.resolve())


def test_hook_doctor_reports_missing_and_installed(tmp_path: Path) -> None:
    ok, _ = auto_hook.hook_doctor(tmp_path)
    assert not ok

    auto_hook.install_hooks(tmp_path)
    ok, detail = auto_hook.hook_doctor(tmp_path)
    assert ok
    assert "installed at" in detail
