from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from djobs import host_contract as contract
from djobs.host_contract import ObservationRequest


def _repository_state(root: Path) -> dict[str, object]:
    return {
        "name": "repo",
        "fingerprint": "family:test-repository",
        "head": "a" * 40,
        "branch": "main",
        "dirty": False,
        "checkout_id": "repo:test-checkout",
        "workspace_fingerprint": "sha256:" + "b" * 64,
        "identity_confidence": "exact",
        "_root": str(root),
        "_scopes": ("family:test-repository", "repo:test-checkout"),
    }


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE agent_observations (
            id TEXT PRIMARY KEY,
            correlation_id TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            session_id_hash TEXT,
            event_type TEXT NOT NULL,
            tool_name TEXT,
            summary TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    return connection


def _insert(
    connection: sqlite3.Connection,
    memory_id: str,
    *,
    kind: str,
    summary: str,
    head: str | None,
    feature_id: str = "feature-a",
    status: str = "active",
    created_at: str = "2026-08-05T08:00:00+00:00",
) -> None:
    metadata = {
        "memory_status": status,
        "stored_as_data": True,
        "repo_family_id": "family:test-repository",
        "checkout_id": "repo:test-checkout",
        "feature_id": feature_id,
        "task_id": "task-a",
        "command": "pytest -q",
        "return_code": 1 if kind == "tool_failure" else 0,
        "affected_files": ["src/example.py"],
    }
    if head is not None:
        metadata["commit_sha"] = head
    connection.execute(
        """
        INSERT INTO agent_observations (
            id, correlation_id, agent_type, session_id_hash, event_type,
            tool_name, summary, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            "family:test-repository",
            "codex",
            "session-hash",
            kind,
            "pytest",
            summary,
            json.dumps(metadata),
            created_at,
        ),
    )


def test_capabilities_make_mutating_operations_unavailable() -> None:
    payload = contract.capabilities_payload()

    assert payload["authority"] == "advisory"
    assert payload["side_effects"] is False
    assert payload["operations"]["observation"]["writes_database"] is False
    assert payload["operations"]["checkpoint"]["available_in_advisory_mode"] is False
    assert payload["operations"]["checkpoint"]["claims_task"] is True
    assert payload["operations"]["handoff"]["changes_task_status"] is True


def test_observation_filters_bind_repository_head_and_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "memory.db"
    connection = _database(database)
    _insert(
        connection,
        "exact-failure",
        kind="tool_failure",
        summary="Current HEAD failed",
        head="a" * 40,
    )
    _insert(
        connection,
        "old-success",
        kind="tool_result",
        summary="Old HEAD passed",
        head="c" * 40,
    )
    _insert(
        connection,
        "other-feature",
        kind="tool_failure",
        summary="Other feature failed",
        head="a" * 40,
        feature_id="feature-b",
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(contract, "repository_state", lambda _cwd: _repository_state(tmp_path))

    payload = contract.build_observation_response(
        ObservationRequest(
            repository_head="a" * 40,
            repository_fingerprint="family:test-repository",
            feature_id="feature-a",
            status="active",
            token_budget=2000,
        ),
        cwd=str(tmp_path),
        db_path=database,
    )

    assert payload["ok"] is True
    assert payload["filter_execution"] == "sqlite_query_stage"
    assert [item["id"] for item in payload["repository_observations"]] == ["exact-failure"]
    item = payload["repository_observations"][0]
    assert item["repository"]["identity_confidence"] == "head_bound"
    assert item["authority"] == "advisory"
    assert item["stored_content_is_data"] is True


def test_observation_query_is_database_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "memory.db"
    connection = _database(database)
    _insert(
        connection,
        "memory-1",
        kind="tool_result",
        summary="Repository evidence",
        head="a" * 40,
    )
    connection.commit()
    connection.close()
    before_bytes = database.read_bytes()
    before_mtime = database.stat().st_mtime_ns
    monkeypatch.setattr(contract, "repository_state", lambda _cwd: _repository_state(tmp_path))

    payload = contract.build_observation_response(
        ObservationRequest(repository_head="a" * 40, token_budget=2000),
        db_path=database,
    )

    assert payload["ok"] is True
    assert database.read_bytes() == before_bytes
    assert database.stat().st_mtime_ns == before_mtime
    assert not database.with_name(database.name + "-wal").exists()
    assert not database.with_name(database.name + "-shm").exists()


def test_budget_discloses_truncation_and_preserves_exact_head_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "memory.db"
    connection = _database(database)
    _insert(
        connection,
        "critical",
        kind="tool_failure",
        summary="critical failure " + "x" * 300,
        head="a" * 40,
    )
    for index in range(12):
        _insert(
            connection,
            f"result-{index}",
            kind="tool_result",
            summary=f"result {index} " + "y" * 300,
            head="a" * 40,
            created_at=f"2026-08-05T07:{index:02d}:00+00:00",
        )
    connection.commit()
    connection.close()
    monkeypatch.setattr(contract, "repository_state", lambda _cwd: _repository_state(tmp_path))

    payload = contract.build_observation_response(
        ObservationRequest(repository_head="a" * 40, max_items=13, token_budget=800),
        db_path=database,
    )

    assert payload["budget"]["truncated"] is True
    assert payload["budget"]["dropped_for_budget_count"] > 0
    assert payload["budget"]["critical_evidence_omitted"] is True
    assert payload["repository_observations"] == []
    assert payload["budget"]["estimated_selected_tokens"] <= 800

    larger = contract.build_observation_response(
        ObservationRequest(repository_head="a" * 40, max_items=13, token_budget=2000),
        db_path=database,
    )
    assert larger["budget"]["critical_evidence_omitted"] is False
    assert larger["repository_observations"][0]["id"] == "critical"


def test_receipt_hash_verifies_and_detects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "memory.db"
    connection = _database(database)
    _insert(
        connection,
        "memory-1",
        kind="tool_result",
        summary="Verified response",
        head="a" * 40,
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(contract, "repository_state", lambda _cwd: _repository_state(tmp_path))
    payload = contract.build_observation_response(
        ObservationRequest(repository_head="a" * 40, token_budget=2000),
        db_path=database,
    )

    verified = contract.verify_receipt_payload(payload)
    assert verified["valid"] is True

    payload["repository_observations"][0]["summary"] = "tampered"
    tampered = contract.verify_receipt_payload(payload)
    assert tampered["valid"] is False


def test_contract_json_is_ascii_safe_under_cp950() -> None:
    encoded = contract.dumps(
        {
            "summary": "中文觀察 ✅",
            "path": "C:\\使用者\\測試專案",
            "branch": "feature/修正-編碼",
        }
    )

    encoded.encode("cp950")
    assert json.loads(encoded)["summary"] == "中文觀察 ✅"


def test_runtime_payloads_validate_against_published_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema_root = Path(contract.__file__).parent / "schemas" / "host_contract" / "v1"
    capability_schema = json.loads(
        (schema_root / "capabilities.schema.json").read_text(encoding="utf-8")
    )
    observation_schema = json.loads(
        (schema_root / "observation.schema.json").read_text(encoding="utf-8")
    )
    database = tmp_path / "memory.db"
    connection = _database(database)
    _insert(
        connection,
        "memory-1",
        kind="tool_result",
        summary="Schema validated",
        head="a" * 40,
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(contract, "repository_state", lambda _cwd: _repository_state(tmp_path))
    observation = contract.build_observation_response(
        ObservationRequest(repository_head="a" * 40, token_budget=2000),
        db_path=database,
    )

    Draft202012Validator(
        capability_schema,
        format_checker=FormatChecker(),
    ).validate(contract.capabilities_payload())
    Draft202012Validator(
        observation_schema,
        format_checker=FormatChecker(),
    ).validate(observation)
