"""Canonical launch description for the compact local djobs MCP server.

The human CLI, host setup, project wiring, and editor integration must all
start the same public entry point.  Historical executables such as
``djobs-mcp`` remain valid compatibility aliases, but new configuration is
written as ``djobs mcp`` (or the equivalent current-interpreter module form).
"""

from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Which = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class McpLaunch:
    command: str
    args: tuple[str, ...]
    source: str

    def argv(self) -> list[str]:
        return [self.command, *self.args]


def _current_scripts_directory() -> Path:
    """Return the console-script directory for the running Python environment."""

    return Path(sysconfig.get_path("scripts"))


def _path_key(value: str | os.PathLike[str]) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _same_environment_command(
    *, scripts_directory: Path, which: Which, platform_name: str
) -> str | None:
    names = (
        ("djobs.exe", "djobs.cmd", "djobs.bat", "djobs") if platform_name == "nt" else ("djobs",)
    )
    for name in names:
        candidate = scripts_directory / name
        if candidate.is_file():
            return str(candidate)

    discovered = which("djobs")
    if discovered and _path_key(Path(discovered).parent) == _path_key(scripts_directory):
        return discovered
    return None


def resolve_compact_mcp_launch(
    *,
    command: str | None = None,
    python: str | None = None,
    portable: bool = False,
    which: Which | None = None,
    executable: str | None = None,
    platform_name: str | None = None,
    scripts_directory: str | os.PathLike[str] | None = None,
) -> McpLaunch:
    """Resolve one compact MCP launch without starting a process.

    Explicit caller choices retain their historical precedence.  New default
    wiring prefers the installed public ``djobs`` command and its ``mcp``
    subcommand.  The module fallback goes through the same public dispatcher,
    so it cannot silently drift from the documented entry point.
    """

    if command:
        return McpLaunch(command=command, args=(), source="explicit-command")
    if python:
        return McpLaunch(
            command=python,
            args=("-m", "djobs.public_cli", "mcp"),
            source="explicit-python",
        )
    if portable:
        platform = platform_name or os.name
        interpreter = (
            "${workspaceFolder}/.venv/Scripts/python"
            if platform == "nt"
            else "${workspaceFolder}/.venv/bin/python"
        )
        return McpLaunch(
            command=interpreter,
            args=("-m", "djobs.public_cli", "mcp"),
            source="portable-python",
        )

    platform = platform_name or os.name
    public_command = _same_environment_command(
        scripts_directory=(
            Path(scripts_directory)
            if scripts_directory is not None
            else _current_scripts_directory()
        ),
        which=which or shutil.which,
        platform_name=platform,
    )
    if public_command:
        return McpLaunch(command=public_command, args=("mcp",), source="public-command")

    return McpLaunch(
        command=executable or sys.executable,
        args=("-m", "djobs.public_cli", "mcp"),
        source="current-python",
    )
