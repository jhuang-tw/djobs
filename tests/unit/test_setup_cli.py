from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from djobs.setup_cli import configure_host, remove_host, setup_command


class FakeRunner:
    def __init__(self, responses: list[tuple[int, str] | int]) -> None:
        self.responses = iter(responses)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        response = next(self.responses)
        if isinstance(response, tuple):
            code, stdout = response
        else:
            code, stdout = response, ""
        return subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")


def _which(name: str) -> str:
    return f"/tools/{name}"


def test_codex_setup_is_idempotent_and_installs_passive_adapter(tmp_path: Path) -> None:
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
    config = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "SessionStart" in config["hooks"]
    assert "UserPromptSubmit" not in config["hooks"]
    assert "Stop" not in config["hooks"]


def test_mcp_failure_does_not_discard_working_passive_adapter(tmp_path: Path) -> None:
    runner = FakeRunner([1, 1])
    result = configure_host(
        "codex",
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )

    assert result["status"] == "partial"
    assert result["mcp"]["status"] == "error"
    assert result["hooks"]["status"] == "configured"
    assert str(result["command"]).startswith("codex mcp add")
    assert (tmp_path / ".codex" / "hooks.json").exists()


def test_gemini_command_uses_user_scope_and_shared_database(tmp_path: Path) -> None:
    command = setup_command("gemini", tmp_path / "shared.db", ["djobs-mcp"])
    assert command[:3] == ["gemini", "mcp", "add"]
    assert "--scope" in command and "user" in command
    assert any(value.startswith("DJOBS_DB=") for value in command)
    assert "DJOBS_AGENT_TYPE=gemini" in command


def test_gemini_setup_detects_existing_registration_from_list(tmp_path: Path) -> None:
    runner = FakeRunner([(0, "✓ djobs: command: djobs-mcp")])
    result = configure_host(
        "gemini",
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )
    assert result["status"] == "configured"
    assert runner.commands == [["/tools/gemini", "mcp", "list"]]
    assert (tmp_path / ".gemini" / "settings.json").exists()


def test_kimi_setup_merges_mcp_and_toml_without_cli_mcp_subcommand(tmp_path: Path) -> None:
    mcp_path = tmp_path / ".kimi-code" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text(
        json.dumps({"mcpServers": {"other": {"url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    result = configure_host(
        "kimi",
        db=tmp_path / "shared.db",
        which=_which,
        server=["python", "-m", "djobs.coding_mcp"],
        home=tmp_path,
    )
    assert result["status"] == "configured"
    saved = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["other"]["url"] == "https://example.test/mcp"
    assert saved["mcpServers"]["djobs"]["command"] == "python"
    assert saved["mcpServers"]["djobs"]["env"]["DJOBS_AGENT_TYPE"] == "kimi"
    assert (tmp_path / ".kimi-code" / "config.toml").exists()


def test_unavailable_client_does_not_change_config(tmp_path: Path) -> None:
    result = configure_host(
        "gemini",
        db=tmp_path / "shared.db",
        which=lambda _: None,
        server=["python", "-m", "djobs.coding_mcp"],
        home=tmp_path,
    )
    assert result["status"] == "manual"
    assert str(result["command"]).startswith("gemini mcp add")
    assert not (tmp_path / ".gemini" / "settings.json").exists()


def test_remove_kimi_preserves_unrelated_mcp_and_config(tmp_path: Path) -> None:
    configure_host(
        "kimi",
        db=tmp_path / "shared.db",
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )
    mcp_path = tmp_path / ".kimi-code" / "mcp.json"
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    data["mcpServers"]["other"] = {"url": "https://example.test/mcp"}
    mcp_path.write_text(json.dumps(data), encoding="utf-8")
    config_path = tmp_path / ".kimi-code" / "config.toml"
    config_path.write_text('theme = "dark"\n\n' + config_path.read_text(), encoding="utf-8")

    result = remove_host("kimi", which=_which, home=tmp_path)
    assert result["status"] == "removed"
    saved = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "djobs" not in saved["mcpServers"]
    assert "other" in saved["mcpServers"]
    text = config_path.read_text(encoding="utf-8")
    assert 'theme = "dark"' in text
    assert "djobs managed observation hooks" not in text
