from __future__ import annotations

import json
import sys
from pathlib import Path

from djobs.host_hooks import (
    _command,
    host_hook_doctor,
    install_host_hooks,
    remove_host_hooks,
)


def test_codex_adapter_is_passive_and_uses_windows_override(tmp_path: Path) -> None:
    result = install_host_hooks("codex", tmp_path / "shared.db", home=tmp_path)
    config = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

    assert set(config["hooks"]) >= {
        "SessionStart",
        "PostToolUse",
        "PreCompact",
        "SessionEnd",
    }
    assert "UserPromptSubmit" not in config["hooks"]
    assert "Stop" not in config["hooks"]
    handler = config["hooks"]["SessionStart"][-1]["hooks"][0]
    assert handler["type"] == "command"
    assert "commandWindows" in handler
    assert "--client codex" in handler["command"]


def test_claude_adapter_merge_is_idempotent_and_preserves_settings(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Read"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]},
            }
        ),
        encoding="utf-8",
    )

    first = install_host_hooks("claude", tmp_path / "shared.db", home=tmp_path)
    content = path.read_text(encoding="utf-8")
    second = install_host_hooks("claude", tmp_path / "shared.db", home=tmp_path)

    assert first["status"] == "configured"
    assert second["status"] == "unchanged"
    assert path.read_text(encoding="utf-8") == content
    saved = json.loads(content)
    assert saved["permissions"] == {"allow": ["Read"]}
    assert "echo keep" in content
    assert content.count("djobs.hook_entrypoint") == 6
    handler = saved["hooks"]["SessionEnd"][-1]["hooks"][0]
    assert handler["command"] == sys.executable
    assert handler["args"][0:3] == ["-m", "djobs.hook_entrypoint", "session-end"]


def test_gemini_adapter_uses_native_events_without_invalid_lifecycle_matchers(
    tmp_path: Path,
) -> None:
    result = install_host_hooks("gemini", tmp_path / "shared.db", home=tmp_path)
    saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert set(saved["hooks"]) >= {
        "SessionStart",
        "BeforeAgent",
        "AfterTool",
        "PreCompress",
        "SessionEnd",
    }
    for event in ("SessionStart", "BeforeAgent", "PreCompress", "SessionEnd"):
        assert "matcher" not in saved["hooks"][event][0]
    handler = saved["hooks"]["AfterTool"][0]["hooks"][0]
    assert handler["timeout"] == 15000
    assert "--client gemini" in handler["command"]


def test_platform_command_quoting_supports_windows_paths_with_spaces() -> None:
    argv = [r"C:\Program Files\Python\python.exe", "-m", "djobs.hook_entrypoint"]
    windows = _command(argv, windows=True)
    posix = _command(argv, windows=False)

    assert windows.startswith('"C:\\Program Files\\Python\\python.exe"')
    assert posix.startswith("'C:\\Program Files\\Python\\python.exe'")


def test_kimi_toml_adapter_injects_once_without_observation_stdout_noise(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".kimi-code" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('default_model = "kimi"\n', encoding="utf-8")

    first = install_host_hooks("kimi", tmp_path / "shared.db", home=tmp_path)
    content = path.read_text(encoding="utf-8")
    second = install_host_hooks("kimi", tmp_path / "shared.db", home=tmp_path)

    assert first["status"] == "configured"
    assert second["status"] == "unchanged"
    assert path.read_text(encoding="utf-8") == content
    assert 'default_model = "kimi"' in content
    assert content.count("[[hooks]]") == 6
    assert 'event = "SessionStart"' in content
    assert 'event = "UserPromptSubmit"' in content
    assert "session-prepare" in content and "--output silent" in content
    assert "prompt-context" in content and "--output plain" in content
    prompt_section = content.split('event = "UserPromptSubmit"', 1)[1].split("[[hooks]]", 1)[0]
    assert "matcher =" not in prompt_section


def test_remove_only_managed_adapter_entries(tmp_path: Path) -> None:
    install_host_hooks("gemini", tmp_path / "shared.db", home=tmp_path)
    path = tmp_path / ".gemini" / "settings.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["hooks"]["AfterTool"][0]["hooks"].insert(0, {"type": "command", "command": "echo keep"})
    path.write_text(json.dumps(config), encoding="utf-8")

    result = remove_host_hooks("gemini", home=tmp_path)
    saved = path.read_text(encoding="utf-8")
    assert result["status"] == "removed"
    assert "echo keep" in saved
    assert "djobs.hook_entrypoint" not in saved
    assert not host_hook_doctor("gemini", home=tmp_path)["installed"]


def test_repair_never_erases_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / ".gemini" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    try:
        install_host_hooks("gemini", tmp_path / "shared.db", home=tmp_path, force=True)
    except ValueError as exc:
        assert "refusing to modify malformed JSON" in str(exc)
    else:
        raise AssertionError("malformed settings must not be overwritten")
    assert path.read_text(encoding="utf-8") == "{broken"
