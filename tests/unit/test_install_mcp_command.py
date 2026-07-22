"""Unit tests for MCP launch-command resolution in ``install-mcp``.

These cover the cross-project wiring fix: the emitted ``mcp.json`` must point at
an interpreter that actually has djobs, even in a project without a local
``.venv``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

from djobs import cli


def _args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {"command": None, "python": None, "portable": False}
    base.update(overrides)
    return argparse.Namespace(**base)


def test_explicit_command_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even if a console script exists, --command takes precedence.
    monkeypatch.setattr("shutil.which", lambda _name: "/somewhere/djobs-mcp")
    cmd, cmd_args = cli._resolve_mcp_command(_args(command="my-djobs"))
    assert cmd == "my-djobs"
    assert cmd_args == []


def test_explicit_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/somewhere/djobs-mcp")
    cmd, cmd_args = cli._resolve_mcp_command(_args(python="/opt/py/bin/python"))
    assert cmd == "/opt/py/bin/python"
    assert cmd_args == ["-m", "djobs.coding_mcp"]


def test_command_beats_python_and_portable() -> None:
    cmd, cmd_args = cli._resolve_mcp_command(
        _args(command="djobs-mcp", python="/x/python", portable=True)
    )
    assert cmd == "djobs-mcp"
    assert cmd_args == []


def test_portable_emits_relocatable_hint() -> None:
    cmd, cmd_args = cli._resolve_mcp_command(_args(portable=True))
    assert "${workspaceFolder}/.venv" in cmd
    assert cmd_args == ["-m", "djobs.coding_mcp"]
    if os.name == "nt":
        assert cmd.endswith("/Scripts/python")
    else:
        assert cmd.endswith("/bin/python")


def test_default_prefers_console_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/local/bin/{name}")
    cmd, cmd_args = cli._resolve_mcp_command(_args())
    assert cmd == "/usr/local/bin/djobs-mcp"
    assert cmd_args == []


def test_default_falls_back_to_current_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    # No console script on PATH -> use the absolute current interpreter, which
    # is guaranteed to import djobs (it is what runs this command).
    monkeypatch.setattr("shutil.which", lambda _name: None)
    cmd, cmd_args = cli._resolve_mcp_command(_args())
    assert cmd == sys.executable
    assert os.path.isabs(cmd)
    assert cmd_args == ["-m", "djobs.coding_mcp"]


def test_install_mcp_writes_resolved_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    out = tmp_path / "mcp.json"
    args = argparse.Namespace(
        full_approve=False,
        print=False,
        force=True,
        output=str(out),
        db=None,
        use_global=False,
        write_instructions=False,
        command=None,
        python="/custom/python",
        portable=False,
    )
    cli._cmd_install_mcp(args)
    data = json.loads(out.read_text(encoding="utf-8"))
    server = data["servers"]["djobs"]
    assert server["command"] == "/custom/python"
    assert server["args"] == ["-m", "djobs.coding_mcp"]
