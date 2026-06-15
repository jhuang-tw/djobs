"""Unit tests for the ``djobs doctor`` setup-diagnostics command."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from djobs import cli


def _run_doctor(as_json: bool) -> None:
    cli._cmd_doctor(argparse.Namespace(as_json=as_json))


def test_doctor_json_lists_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    _run_doctor(as_json=True)
    data = json.loads(capsys.readouterr().out)
    names = [c["name"] for c in data["checks"]]
    assert any("package" in n for n in names)
    assert any("db" in n.lower() for n in names)
    assert any("mcp.json" in n for n in names)
    # Every check carries an explicit boolean + human detail.
    for check in data["checks"]:
        assert isinstance(check["ok"], bool)
        assert check["detail"]


def test_doctor_json_reports_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import djobs

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    _run_doctor(as_json=True)
    data = json.loads(capsys.readouterr().out)
    # Top-level version field matches the importable package version.
    assert data["version"] == djobs.__version__


def test_doctor_human_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    _run_doctor(as_json=False)  # djobs importable + db writable => no SystemExit
    out = capsys.readouterr().out
    assert "doctor" in out.lower()
    assert "[OK  ]" in out


def test_doctor_reports_present_mcp_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "mcp.json").write_text(
        json.dumps({"servers": {"djobs": {"command": "djobs-mcp", "args": []}}}),
        encoding="utf-8",
    )
    _run_doctor(as_json=True)
    data = json.loads(capsys.readouterr().out)
    wiring = next(c for c in data["checks"] if c["name"] == "mcp.json wiring")
    assert "djobs-mcp" in wiring["detail"]


# --- agent guidance block -------------------------------------------------


def test_doctor_reports_missing_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    _run_doctor(as_json=True)
    data = json.loads(capsys.readouterr().out)
    guidance = next(c for c in data["checks"] if c["name"] == "agent guidance block")
    assert guidance["ok"] is False
    assert "install-instructions" in guidance["detail"]


def test_doctor_reports_present_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    cli._write_instructions_to(tmp_path / ".github" / "copilot-instructions.md")
    capsys.readouterr()  # drop the write message
    _run_doctor(as_json=True)
    data = json.loads(capsys.readouterr().out)
    guidance = next(c for c in data["checks"] if c["name"] == "agent guidance block")
    assert guidance["ok"] is True
    assert ".github/copilot-instructions.md" in guidance["detail"]


def test_doctor_missing_guidance_is_not_critical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No instruction block present, but pkg + db are fine -> must NOT exit non-zero.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    _run_doctor(as_json=False)  # would raise SystemExit if guidance were critical


# --- info-level (advisory) checks -----------------------------------------


def test_doctor_mcp_on_path_is_info_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # When djobs-mcp is not on PATH the wiring still works via the interpreter,
    # so that check must be advisory (level=info), never a failure.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    _run_doctor(as_json=True)
    data = json.loads(capsys.readouterr().out)
    mcp_check = next(c for c in data["checks"] if c["name"] == "djobs-mcp on PATH")
    assert mcp_check["ok"] is False
    assert mcp_check["level"] == "info"


def test_doctor_human_output_uses_info_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A successful setup with no djobs-mcp on PATH must not print a scary [FAIL]
    # for that advisory line — it renders as [INFO].
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    _run_doctor(as_json=False)
    out = capsys.readouterr().out
    # The djobs-mcp line is present and marked INFO, not FAIL.
    mcp_line = next(ln for ln in out.splitlines() if "djobs-mcp on PATH" in ln)
    assert "[INFO]" in mcp_line
    assert "[FAIL]" not in mcp_line


# --- _probe_command -------------------------------------------------------


def test_probe_command_empty() -> None:
    ok, detail = cli._probe_command("")
    assert ok is False
    assert "empty" in detail


def test_probe_command_workspace_folder_hint() -> None:
    ok, detail = cli._probe_command("${workspaceFolder}/.venv/bin/python")
    assert ok is True
    assert "relocatable" in detail


def test_probe_command_absolute_existing() -> None:
    ok, detail = cli._probe_command(sys.executable)
    assert ok is True
    assert "found" in detail


def test_probe_command_absolute_missing(tmp_path: Path) -> None:
    bogus = str(tmp_path / "nope" / "python.exe")
    ok, detail = cli._probe_command(bogus)
    assert ok is False
    assert "MISSING" in detail


# --- _probe_db_writable ---------------------------------------------------


def test_probe_db_writable_existing(tmp_path: Path) -> None:
    db = tmp_path / "exists.db"
    sqlite3.connect(str(db)).close()
    ok, detail = cli._probe_db_writable(db)
    assert ok is True
    assert "writable" in detail


def test_probe_db_writable_new_in_writable_parent(tmp_path: Path) -> None:
    ok, detail = cli._probe_db_writable(tmp_path / "sub" / "new.db")
    assert ok is True
    assert "created" in detail
