from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from djobs.diagnostics import (
    clear_diagnostics,
    list_diagnostics,
    record_failure,
    record_shared_failure,
)
from djobs.privacy import redact
from djobs.storage.sqlite import SQLiteJobRepository


def test_redaction_covers_high_risk_credential_shapes() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----"
    raw = " ".join(
        [
            "Authorization: Bearer header-secret",
            "github_pat_abcdefghijklmnopqrstuvwxyz123456",
            "AKIAABCDEFGHIJKLMNOP",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
            "postgresql://user:db-password@localhost/db",
            "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz",
            pem,
        ]
    )

    result = redact(raw)

    for secret in (
        "header-secret",
        "github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
        "db-password",
        "sk-proj-abcdefghijklmnopqrstuvwxyz",
        "abc123",
    ):
        assert secret not in result.text
    assert result.redaction_count >= 7
    assert "pem_private_key" in result.categories
    assert "github_token" in result.categories
    assert "jwt" in result.categories


def test_redaction_does_not_count_already_redacted_placeholders_twice() -> None:
    result = redact("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz")

    assert result.text == "OPENAI_API_KEY=<redacted>"
    assert result.redaction_count == 1
    assert result.categories == ("openai_api_key",)


def test_fail_open_diagnostics_are_deduplicated_redacted_and_clearable(tmp_path: Path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "diagnostics.db")

    for _ in range(3):
        record_failure(
            repo,
            "lifecycle.tool_observation",
            RuntimeError("database locked with API_KEY=super-secret"),
            context={"command": "curl -H 'Authorization: Bearer token-value'"},
        )

    items = list_diagnostics(repo)

    assert len(items) == 1
    assert items[0]["component"] == "lifecycle.tool_observation"
    assert items[0]["occurrence_count"] == 3
    assert "super-secret" not in items[0]["last_message"]
    assert "token-value" not in str(items[0]["context"])
    assert clear_diagnostics(repo) == 1
    assert list_diagnostics(repo) == []


def test_diagnostics_work_on_a_fresh_queue_database(tmp_path: Path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "fresh.db")

    record_failure(repository, "lifecycle.startup", RuntimeError("token=plain-secret"))

    diagnostics = list_diagnostics(repository)
    assert len(diagnostics) == 1
    assert diagnostics[0]["component"] == "lifecycle.startup"
    assert "plain-secret" not in diagnostics[0]["last_message"]


def test_shared_diagnostics_remain_fast_when_database_is_locked(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "locked.db"
    repository = SQLiteJobRepository.from_path(database)
    repository.close()
    monkeypatch.setenv("DJOBS_DB", str(database))

    lock = sqlite3.connect(database)
    lock.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        record_shared_failure("lifecycle.locked", RuntimeError("database is locked"))
    finally:
        elapsed = time.monotonic() - started
        lock.rollback()
        lock.close()

    assert elapsed < 1.0
