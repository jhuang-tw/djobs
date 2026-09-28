"""Installed-wheel smoke contract tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_install.py"
_SPEC = importlib.util.spec_from_file_location("smoke_install", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_installed_smoke_expects_memory_first_top_level_help() -> None:
    assert _MODULE._TOP_LEVEL_HELP_MARKERS == (
        "Local repository memory",
        "djobs setup",
        "djobs doctor",
        "djobs memory list",
        "djobs legacy --help",
    )
    assert "djobs repair" not in _MODULE._TOP_LEVEL_HELP_MARKERS
    assert "djobs remove" not in _MODULE._TOP_LEVEL_HELP_MARKERS


def test_smoke_runner_captures_utf8_without_locale_reader_failures(tmp_path, monkeypatch):
    import locale
    import os
    import subprocess
    import sys
    import threading

    errors = []
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp950")
    if hasattr(subprocess, "_text_encoding"):
        monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp950")
    monkeypatch.setattr(
        threading, "excepthook", lambda args: errors.append(args.exc_type.__name__)
    )
    expected = "UTF8 中文 ✅"
    program = "import sys; sys.stdout.buffer.write(" + repr(expected.encode("utf-8")) + ")"
    result = _MODULE._run([sys.executable, "-c", program], env=os.environ.copy(), cwd=tmp_path)
    assert result.stdout == expected
    assert not errors


def test_smoke_runner_rejects_bad_bytes_even_when_process_succeeds(tmp_path):
    import os
    import sys

    import pytest

    with pytest.raises(RuntimeError, match="invalid UTF-8"):
        _MODULE._run(
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes([255]))"],
            env=os.environ.copy(),
            cwd=tmp_path,
        )
