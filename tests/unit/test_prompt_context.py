from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from djobs import handoff, lifecycle
from djobs.hook_entrypoint import _emit
from djobs.observations import recent_observations
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_workspace


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    return path


def test_prompt_context_is_once_per_session_and_resets_on_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
    database = tmp_path / "shared.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))
    payload = {"cwd": str(root), "session_id": "kimi-session"}

    lifecycle.prepare_prompt_context(payload, agent_type="kimi")
    first = lifecycle.prompt_context(payload, agent_type="kimi")
    second = lifecycle.prompt_context(payload, agent_type="kimi")

    assert "no task was claimed automatically" in first["additionalContext"]
    assert second == {}

    lifecycle.prepare_prompt_context(payload, agent_type="kimi")
    resumed = lifecycle.prompt_context(payload, agent_type="kimi")
    assert "repository_change" in resumed["additionalContext"]

    workspace = resolve_workspace(cwd=str(root))
    visible = recent_observations(repository, workspace, limit=20)
    assert all(item["event"] != "context_injected" for item in visible)


def test_redaction_covers_prefixed_quoted_json_and_flag_credentials() -> None:
    raw = (
        "OPENAI_API_KEY=sk-openai "
        "GITHUB_TOKEN='ghp quoted secret' "
        'AWS_SECRET_ACCESS_KEY="aws secret value" '
        "--password 'cli secret' "
        'https://user:url-secret@example.test Authorization: Bearer bearer-secret '
        '{"client_secret":"json secret"}'
    )

    redacted = lifecycle._redact(raw)

    for secret in (
        "sk-openai",
        "ghp quoted secret",
        "aws secret value",
        "cli secret",
        "url-secret",
        "bearer-secret",
        "json secret",
    ):
        assert secret not in redacted
    assert redacted.count("<redacted>") >= 7
    assert '"client_secret":"<redacted>"' in redacted


def test_redaction_does_not_hide_normal_security_vocabulary() -> None:
    text = "Explain the secret value, token budget, and password policy."
    assert lifecycle._redact(text) == text


def test_adapter_output_modes_do_not_emit_json_noise(capsys: pytest.CaptureFixture[str]) -> None:
    result = {"additionalContext": "repository context", "ok": True}

    _emit(result, "silent")
    assert capsys.readouterr().out == ""

    _emit(result, "plain")
    assert capsys.readouterr().out == "repository context"

    _emit(result, "json")
    output = capsys.readouterr().out
    assert '"additionalContext":"repository context"' in output
