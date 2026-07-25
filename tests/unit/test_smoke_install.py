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
