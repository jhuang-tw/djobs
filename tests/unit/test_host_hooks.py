from __future__ import annotations

import json
import sys
from pathlib import Path

from djobs.host_hooks import host_hook_doctor, install_host_hooks, remove_host_hooks


def test_codex_hook_config_uses_official_events_and_windows_override(tmp_path: Path) -> None:
    result = install_host_hooks("codex", tmp_path / "shared.db", home=tmp_path)
    config = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

    assert set(config["hooks"]) >= {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
    }
    session = config["hooks"]["SessionStart"][-1]
    assert session["matcher"] == "startup|resume|clear|compact"
    handler = session["hooks"][0]
    assert handler["type"] == "command"
    assert "commandWindows" in handler
    assert "--host codex" in handler["command"]
    assert str((tmp_path / "shared.db").resolve()) in handler["command"]


def test_claude_settings_merge_is_idempotent_and_preserves_settings(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Read"]},
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "echo existing"}]}
                    ]
                },
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
    assert "echo existing" in content
    assert content.count("djobs.hook_entrypoint") == 5
    handler = saved["hooks"]["UserPromptSubmit"][-1]["hooks"][0]
    assert handler["command"] == sys.executable
    assert handler["args"][0:3] == ["-m", "djobs.hook_entrypoint", "user-prompt"]


def test_remove_only_managed_hook_handlers(tmp_path: Path) -> None:
    install_host_hooks("claude", tmp_path / "shared.db", home=tmp_path)
    path = tmp_path / ".claude" / "settings.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["hooks"]["PreToolUse"][0]["hooks"].insert(
        0, {"type": "command", "command": "echo keep"}
    )
    path.write_text(json.dumps(config), encoding="utf-8")

    result = remove_host_hooks("claude", home=tmp_path)
    saved = path.read_text(encoding="utf-8")

    assert result["status"] == "removed"
    assert "echo keep" in saved
    assert "djobs.hook_entrypoint" not in saved
    assert not host_hook_doctor("claude", home=tmp_path)["installed"]
