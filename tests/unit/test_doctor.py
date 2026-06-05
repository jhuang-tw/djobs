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


def test_doctor_json_never_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Even with no mcp.json wiring present, --json returns instead of sys.exit.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))
    _run_doctor(as_json=True)  # must not raise SystemExit
    capsys.readouterr()


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
