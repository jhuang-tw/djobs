from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import djobs.setup_cli as setup_cli
from djobs.host_hooks import host_hook_doctor, install_host_hooks, remove_host_hooks
from djobs.setup_cli import configure_host, setup_command


class FakeRunner:
    def __init__(self, responses: list[int | tuple[int, str]]) -> None:
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


def test_copilot_hook_file_uses_native_versioned_format(tmp_path: Path) -> None:
    result = install_host_hooks("copilot", tmp_path / "shared.db", home=tmp_path)
    path = Path(result["path"])
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert path == tmp_path / ".copilot" / "hooks" / "djobs.json"
    assert saved["version"] == 1
    assert set(saved["hooks"]) == {
        "SessionStart",
        "UserPromptSubmit",
        "PostToolUse",
        "PostToolUseFailure",
        "PreCompact",
        "SessionEnd",
    }
    assert "user-prompt" in saved["hooks"]["UserPromptSubmit"][0]["bash"]
    assert "Stop" not in saved["hooks"]
    handler = saved["hooks"]["SessionStart"][0]
    assert handler["type"] == "command"
    assert "bash" in handler and "powershell" in handler
    assert handler["timeoutSec"] == 15
    assert "--client copilot" in handler["bash"]
    assert host_hook_doctor("copilot", home=tmp_path)["installed"]


def test_copilot_hook_install_is_idempotent_and_removable(tmp_path: Path) -> None:
    first = install_host_hooks("copilot", tmp_path / "shared.db", home=tmp_path)
    content = Path(first["path"]).read_text(encoding="utf-8")
    second = install_host_hooks("copilot", tmp_path / "shared.db", home=tmp_path)

    assert first["status"] == "configured"
    assert second["status"] == "unchanged"
    assert Path(first["path"]).read_text(encoding="utf-8") == content
    removed = remove_host_hooks("copilot", home=tmp_path)
    assert removed["status"] == "removed"
    assert not Path(first["path"]).exists()


def test_copilot_does_not_replace_unmanaged_hook_file(tmp_path: Path) -> None:
    path = tmp_path / ".copilot" / "hooks" / "djobs.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": 1, "hooks": {"SessionStart": []}}),
        encoding="utf-8",
    )

    try:
        install_host_hooks("copilot", tmp_path / "shared.db", home=tmp_path)
    except ValueError as exc:
        assert "unmanaged Copilot hook file" in str(exc)
    else:
        raise AssertionError("unmanaged file must not be overwritten")


def test_copilot_mcp_command_is_scoped_and_tool_limited(tmp_path: Path) -> None:
    command = setup_command("copilot", tmp_path / "shared.db", ["djobs-mcp"])

    assert command[:4] == ["copilot", "mcp", "add", "djobs"]
    assert "DJOBS_AGENT_TYPE=copilot" in command
    assert "--tools" in command
    assert command[command.index("--tools") + 1] == (
        "sync_workspace,memory,checkpoint,handoff,resume_delta"
    )
    assert command[command.index("--") + 1 :] == ["djobs-mcp"]


def test_copilot_setup_configures_mcp_and_shared_adapter(tmp_path: Path) -> None:
    runner = FakeRunner([1, 0])
    result = configure_host(
        "copilot",
        db=tmp_path / "shared.db",
        runner=runner,
        which=_which,
        server=["djobs-mcp"],
        home=tmp_path,
    )

    assert result["status"] == "configured"
    assert result["mcp"]["status"] == "configured"
    assert result["hooks"]["status"] == "configured"
    assert runner.commands[0] == ["/tools/copilot", "mcp", "get", "djobs"]
    assert runner.commands[1][0:4] == ["/tools/copilot", "mcp", "add", "djobs"]


def test_setup_defaults_to_copilot(monkeypatch) -> None:
    called: list[str] = []

    def fake(host: str, **_: Any) -> dict[str, object]:
        called.append(host)
        return {"status": "unchanged", "message": "ok"}

    monkeypatch.setattr(setup_cli, "configure_host", fake)
    assert setup_cli.main(["setup"]) == 0
    assert called == ["copilot"]


def test_copilot_setup_without_cli_still_installs_vscode_adapter(tmp_path: Path) -> None:
    result = configure_host(
        "copilot",
        db=tmp_path / "shared.db",
        which=lambda _name: None,
        server=["djobs-mcp"],
        home=tmp_path,
    )

    assert result["status"] == "configured"
    assert result["mcp"]["status"] == "unavailable"
    assert result["hooks"]["status"] == "configured"
    assert (tmp_path / ".copilot" / "hooks" / "djobs.json").exists()
    assert "VS Code Agent" in str(result["message"])
