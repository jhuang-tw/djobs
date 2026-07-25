from __future__ import annotations

import json
import threading
from pathlib import Path

from djobs.host_hooks import install_host_hooks, remove_host_hooks

ROOT = Path(__file__).resolve().parents[2]


def test_concurrent_host_hook_updates_preserve_valid_config(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "hooks.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [{"type": "command", "command": "keep-me"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    errors: list[Exception] = []

    def install() -> None:
        try:
            install_host_hooks("codex", tmp_path / "memory.db", home=tmp_path)
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=install) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    document = json.loads(config.read_text(encoding="utf-8"))
    serialized = json.dumps(document)
    assert "keep-me" in serialized
    assert serialized.count("djobs.hook_entrypoint") == 8
    assert not config.with_name(".hooks.json.djobs.lock").exists()
    assert not list(config.parent.glob(".hooks.json.*.tmp"))


def test_remove_preserves_unmanaged_host_hooks(tmp_path: Path) -> None:
    install_host_hooks("claude", tmp_path / "memory.db", home=tmp_path)
    config = tmp_path / ".claude" / "settings.json"
    document = json.loads(config.read_text(encoding="utf-8"))
    document["hooks"].setdefault("SessionStart", []).insert(
        0,
        {
            "matcher": "startup",
            "hooks": [{"type": "command", "command": "keep-me"}],
        },
    )
    config.write_text(json.dumps(document), encoding="utf-8")

    result = remove_host_hooks("claude", home=tmp_path)

    assert result["status"] == "removed"
    remaining = json.loads(config.read_text(encoding="utf-8"))
    serialized = json.dumps(remaining)
    assert "keep-me" in serialized
    assert "djobs.hook_entrypoint" not in serialized
    assert not config.with_name(".settings.json.djobs.lock").exists()


def test_vscode_extension_refuses_untrusted_workspaces() -> None:
    manifest = json.loads((ROOT / "vscode-ext" / "package.json").read_text(encoding="utf-8"))
    source = (ROOT / "vscode-ext" / "src" / "extension.ts").read_text(encoding="utf-8")

    capability = manifest["capabilities"]["untrustedWorkspaces"]
    assert capability["supported"] is False
    assert "if (!vscode.workspace.isTrusted)" in source
    assert "runTrusted" in source
    assert "onDidGrantWorkspaceTrust" in source
