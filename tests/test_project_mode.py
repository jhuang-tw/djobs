from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from djobs import project_mode


def _completed(stdout: str = "", *, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["arun"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_doctor_is_explicitly_optional(monkeypatch, capsys) -> None:
    monkeypatch.setattr(project_mode, "arun_executable", lambda: None)

    assert project_mode.main(["doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["memory_available_without_arun"] is True


def test_resolve_project_id_uses_canonical_root(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []

    def fake_run(arguments, *, root, timeout=30.0):
        calls.append((list(arguments), Path(root)))
        return _completed('{"selected_project_id":"project-123"}')

    monkeypatch.setattr(project_mode, "_run_arun", fake_run)

    assert project_mode.resolve_project_id(tmp_path / ".") == "project-123"
    expected = tmp_path.resolve()
    assert calls == [
        (["control", "resolve", "--root", str(expected)], expected),
    ]


def test_init_refuses_duplicate_project(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(project_mode, "resolve_project_id", lambda root: "project-existing")

    code = project_mode.main(
        [
            "init",
            "--root",
            str(tmp_path),
            "--objective",
            "Finish the parser",
            "--acceptance",
            "Focused tests pass",
        ]
    )

    assert code == 2
    assert "already owns this root" in capsys.readouterr().err


def test_init_forwards_only_bounded_create_arguments(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(project_mode, "resolve_project_id", lambda root: None)
    seen: list[list[str]] = []

    def fake_run(arguments, *, root, timeout=30.0):
        seen.append(list(arguments))
        return _completed("PROJECT_ID:project-new\n")

    monkeypatch.setattr(project_mode, "_run_arun", fake_run)

    code = project_mode.main(
        [
            "init",
            "--root",
            str(tmp_path),
            "--objective",
            "Finish the parser",
            "--constraint",
            "No broad refactor",
            "--acceptance",
            "Focused tests pass",
            "--acceptance",
            "No unrelated changes",
        ]
    )

    assert code == 0
    root = str(tmp_path.resolve())
    assert seen == [
        [
            "create",
            "--root",
            root,
            "--objective",
            "Finish the parser",
            "--constraint",
            "No broad refactor",
            "--acceptance",
            "Focused tests pass",
            "--acceptance",
            "No unrelated changes",
        ]
    ]
    assert "PROJECT_ID:project-new" in capsys.readouterr().out


def test_status_resolves_then_requests_read_only_control_status(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(project_mode, "resolve_project_id", lambda root: "project-123")
    seen: list[list[str]] = []

    def fake_run(arguments, *, root, timeout=30.0):
        seen.append(list(arguments))
        return _completed('{"status":"running"}\n')

    monkeypatch.setattr(project_mode, "_run_arun", fake_run)

    assert project_mode.main(["status", "--root", str(tmp_path)]) == 0
    assert seen == [["control", "status", "project-123"]]
    assert json.loads(capsys.readouterr().out)["status"] == "running"


def test_next_is_explicit_and_does_not_execute_an_executor(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(project_mode, "resolve_project_id", lambda root: "project-123")
    seen: list[list[str]] = []

    def fake_run(arguments, *, root, timeout=30.0):
        seen.append(list(arguments))
        return _completed('{"kind":"control-turn","execution_mode":"external"}\n')

    monkeypatch.setattr(project_mode, "_run_arun", fake_run)

    assert project_mode.main(["next", "--root", str(tmp_path)]) == 0
    assert seen == [["control", "next", "project-123"]]
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_mode"] == "external"


def test_nonzero_arun_result_is_reported_without_retry(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = 0

    def fake_run(arguments, *, root, timeout=30.0):
        nonlocal calls
        calls += 1
        return _completed(returncode=3, stderr="bad state")

    monkeypatch.setattr(project_mode, "_run_arun", fake_run)

    with pytest.raises(project_mode.ProjectModeError, match="bad state"):
        project_mode.resolve_project_id(tmp_path)
    assert calls == 1
