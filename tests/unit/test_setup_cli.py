from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from djobs.setup_cli import configure_host, setup_command


class FakeRunner:
    def __init__(self, responses: list[int]) -> None:
        self.responses = iter(responses)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(command, next(self.responses), stdout="", stderr="")


def _which(name: str) -> str:
    return f"/tools/{name}"


def test_setup_is_idempotent_and_does_not_touch_other_servers(tmp_path: Path) -> None:
    runner = FakeRunner([0])

    result = configure_host(
        "codex",
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
    )

    assert result["status"] == "unchanged"
    assert runner.commands == [["/tools/codex", "mcp", "get", "djobs"]]


def test_repair_replaces_only_the_named_djobs_server(tmp_path: Path) -> None:
    runner = FakeRunner([0, 0, 0])

    result = configure_host(
        "claude",
        repair=True,
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
    )

    assert result["status"] == "configured"
    assert runner.commands[1] == ["/tools/claude", "mcp", "remove", "djobs"]
    assert runner.commands[2][0:4] == ["/tools/claude", "mcp", "add", "djobs"]
    assert all("other-server" not in command for command in runner.commands)


def test_setup_prints_copyable_command_when_host_is_unavailable(tmp_path: Path) -> None:
    result = configure_host(
        "codex",
        db=tmp_path / "shared.db",
        which=lambda _: None,
        server=["python", "-m", "djobs.coding_mcp"],
    )

    assert result["status"] == "manual"
    command = str(result["command"])
    assert command.startswith("codex mcp add djobs")
    assert "DJOBS_DB=" in command


def test_codex_and_claude_commands_share_the_same_database(tmp_path: Path) -> None:
    database = tmp_path / "shared.db"

    codex = setup_command("codex", database, ["djobs-mcp"])
    claude = setup_command("claude", database, ["djobs-mcp"])

    codex_env = next(value for value in codex if value.startswith("DJOBS_DB="))
    claude_env = next(value for value in claude if value.startswith("DJOBS_DB="))
    assert codex_env == claude_env
    assert "DJOBS_AGENT_TYPE=codex" in codex
    assert "DJOBS_AGENT_TYPE=claude" in claude
