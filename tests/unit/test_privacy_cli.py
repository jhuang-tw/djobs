from __future__ import annotations

import json
from pathlib import Path

from djobs.commands import privacy


def test_privacy_scan_reports_categories_without_returning_secret(tmp_path: Path, capsys) -> None:
    secret = "github_pat_abcdefghijklmnopqrstuvwxyz123456"
    (tmp_path / "config.txt").write_text(f"TOKEN={secret}\n", encoding="utf-8")
    (tmp_path / "clean.txt").write_text("nothing sensitive\n", encoding="utf-8")

    assert privacy.run(["scan", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["files_with_findings"] == 1
    assert payload["secrets_returned"] is False
    assert secret not in json.dumps(payload)
    assert payload["findings"][0]["path"] == "config.txt"


def test_privacy_test_redaction_never_echoes_original_secret(capsys) -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz"

    assert privacy.run(["test-redaction", f"OPENAI_API_KEY={secret}", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert secret not in payload["redacted"]
    assert payload["redaction_count"] >= 1


def test_privacy_scan_includes_gitignored_env_files(tmp_path: Path, capsys) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    secret = "sk-proj-ignored-abcdefghijklmnopqrstuvwxyz"
    other_secret = "github_pat_untrackedabcdefghijklmnopqrstuvwxyz"
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    (tmp_path / "local-config.txt").write_text(f"TOKEN={other_secret}\n", encoding="utf-8")

    assert privacy.run(["scan", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    paths = {item["path"] for item in payload["findings"]}
    assert {".env", "local-config.txt"} <= paths
    assert secret not in json.dumps(payload)
    assert other_secret not in json.dumps(payload)
