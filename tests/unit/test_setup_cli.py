from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from djobs.setup_cli import configure_host, remove_host, setup_command


class FakeRunner:
    def __init__(self, responses: list[int]) -> None:
        self.responses = iter(responses)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(command, next(self.responses), stdout="", stderr="")


def _which(name: str) -> str:
    return f"/tools/{name}"


def test_setup_is_idempotent_and_installs_hooks_without_touching_other_servers(
    tmp_path: Path,
) -> None:
    runner = FakeRunner([0, 0])

    first = configure_host(
        "codex",
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )
    second = configure_host(
        "codex",
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )

    assert first["status"] == "configured"
    assert second["status"] == "unchanged"
    assert runner.commands == [
        ["/tools/codex", "mcp", "get", "djobs"],
        ["/tools/codex", "mcp", "get", "djobs"],
    ]
    config = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "SessionStart" in config["hooks"]
    assert "UserPromptSubmit" in config["hooks"]
    assert "djobs.hook_entrypoint" in json.dumps(config)


def test_repair_replaces_only_the_named_djobs_server_and_managed_hooks(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read",
                            "hooks": [{"type": "command", "command": "echo keep"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    runner = FakeRunner([0, 0, 0])

    result = configure_host(
        "claude",
        repair=True,
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )

    assert result["status"] == "configured"
    assert runner.commands[1] == ["/tools/claude", "mcp", "remove", "djobs"]
    assert runner.commands[2][0:4] == ["/tools/claude", "mcp", "add", "djobs"]
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["theme"] == "dark"
    assert "echo keep" in json.dumps(saved)
    assert "djobs.hook_entrypoint" in json.dumps(saved)


def test_setup_prints_copyable_command_when_host_is_unavailable(tmp_path: Path) -> None:
    result = configure_host(
        "codex",
        db=tmp_path / "shared.db",
        which=lambda _: None,
        server=["python", "-m", "djobs.coding_mcp"],
        home=tmp_path,
    )

    assert result["status"] == "manual"
    command = str(result["command"])
    assert command.startswith("codex mcp add djobs")
    assert "DJOBS_DB=" in command
    assert not (tmp_path / ".codex" / "hooks.json").exists()


def test_codex_and_claude_commands_share_the_same_database(tmp_path: Path) -> None:
    database = tmp_path / "shared.db"

    codex = setup_command("codex", database, ["djobs-mcp"])
    claude = setup_command("claude", database, ["djobs-mcp"])

    codex_env = next(value for value in codex if value.startswith("DJOBS_DB="))
    claude_env = next(value for value in claude if value.startswith("DJOBS_DB="))
    assert codex_env == claude_env
    assert "DJOBS_AGENT_TYPE=codex" in codex
    assert "DJOBS_AGENT_TYPE=claude" in claude


def test_remove_preserves_unrelated_hooks(tmp_path: Path) -> None:
    runner = FakeRunner([0, 0])
    configure_host(
        "codex",
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )
    path = tmp_path / ".codex" / "hooks.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["hooks"]["Stop"].insert(
        0, {"hooks": [{"type": "command", "command": "echo keep"}]}
    )
    path.write_text(json.dumps(config), encoding="utf-8")

    remove_runner = FakeRunner([0, 0])
    result = remove_host("codex", runner=remove_runner, which=_which, home=tmp_path)

    assert result["status"] == "removed"
    saved = json.loads(path.read_text(encoding="utf-8"))
    text = json.dumps(saved)
    assert "echo keep" in text
    assert "djobs.hook_entrypoint" not in text
