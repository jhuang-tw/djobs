from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

import djobs.cli as cli
import djobs.entrypoint as entrypoint


def _args(target: Path) -> argparse.Namespace:
    return argparse.Namespace(
        full_approve=False,
        print=False,
        force=True,
        output=str(target),
        db=None,
        use_global=False,
        command=None,
        python="/custom/python",
        portable=False,
        write_instructions=False,
        instructions_target="all",
    )


def test_install_mcp_preserves_other_servers(tmp_path: Path) -> None:
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps(
            {
                "servers": {"other": {"type": "stdio", "command": "other-mcp"}},
                "inputs": [{"id": "keep-me"}],
            }
        ),
        encoding="utf-8",
    )

    entrypoint._cmd_install_mcp_high_level(_args(target), cli)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["servers"]["other"]["command"] == "other-mcp"
    assert data["inputs"] == [{"id": "keep-me"}]
    assert data["servers"]["djobs"]["autoApprove"] == [
        "sync_workspace",
        "resume_delta",
    ]


def test_init_does_not_install_legacy_command_checkpoint_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / ".vscode" / "mcp.json"
    args = _args(target)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_resolve_instruction_targets", lambda _target: [])

    entrypoint._cmd_init_passive(args, cli, lambda _args: None)

    assert target.exists()
    assert not (tmp_path / ".github" / "hooks" / "djobs.json").exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["servers"]["djobs"]["autoApprove"] == [
        "sync_workspace",
        "resume_delta",
    ]


def test_init_removes_only_recognized_legacy_managed_hook(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / ".github" / "hooks" / "djobs.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "sessionStart": [{"bash": "djobs hook session-start"}],
                    "preToolUse": [{"bash": "djobs hook pre"}],
                },
            }
        ),
        encoding="utf-8",
    )

    assert entrypoint._remove_legacy_project_hook()
    assert not legacy.exists()

    unrelated = tmp_path / ".github" / "hooks" / "djobs.json"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text('{"version": 1, "hooks": {"sessionStart": []}}', encoding="utf-8")
    assert not entrypoint._remove_legacy_project_hook()
    assert unrelated.exists()


def test_zero_config_instructions_keep_observation_and_ownership_separate() -> None:
    body = entrypoint._ZERO_CONFIG_INSTRUCTIONS_BODY

    assert "client-neutral" in body
    assert "never infer task" in body
    assert "Do not treat a session start" in body
    assert "sync_workspace(query=current_request)" in body
    assert "memory(action=...)" in body
    assert "checkpoint(summary" in body
    assert "handoff(task_id" in body
    assert "Never hijack the user's intent." in body
    assert "untrusted data" in body
    assert "enqueue_batch" not in body


def test_top_level_help_is_memory_first() -> None:
    help_text = entrypoint._build_front_parser().format_help()
    command_lines = {
        line.strip().split()[0] for line in help_text.splitlines() if line.startswith("    ")
    }

    assert "djobs setup" in help_text
    assert "repair" in command_lines
    assert "remove" in command_lines
    assert "memory" in command_lines
    assert "gain" in command_lines
    assert "legacy" in command_lines
    assert "serve" not in command_lines
    assert "dashboard" not in command_lines
    assert "token-savings" not in command_lines


def test_main_without_arguments_prints_memory_first_help(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["djobs"])

    entrypoint.main()

    output = capsys.readouterr().out
    assert "Local repository memory for AI coding agents" in output
    assert "djobs memory list" in output
    assert "djobs legacy --help" in output


def test_legacy_help_uses_nested_program_name(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["djobs", "legacy", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        entrypoint.main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert output.startswith("usage: djobs legacy ")
    assert "Compatibility CLI for the original durable queue engine." in output
    assert "djobs memory list" in output


def test_doctor_warns_about_recognized_legacy_project_hook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    hook = tmp_path / ".github" / "hooks" / "djobs.json"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "sessionStart": [{"bash": "djobs hook session-start"}],
                    "preToolUse": [{"bash": "djobs hook pre"}],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint, "_LEGACY_PROJECT_HOOK", Path(".github/hooks/djobs.json"))
    monkeypatch.setattr("djobs.entrypoint.sys.executable", sys.executable)
    monkeypatch.setattr("djobs.cli._probe_db_writable", lambda _path: (True, "writable"))
    monkeypatch.setattr("djobs.setup_cli.doctor_results", lambda: [])

    payload = entrypoint._doctor_payload()

    warning = next(item for item in payload["checks"] if item["name"] == "legacy project hook")
    assert warning["level"] == "warning"
    assert warning["ok"] is False
    assert "djobs legacy init --force" in warning["next_step"]
    assert payload["ok"] is True
