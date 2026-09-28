from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from djobs.artifacts import ArtifactMemory
from djobs.memory_artifacts import ArtifactError
from djobs.memory_review import ReviewGate
from djobs.observations import clear_workspace_memory, search_observations
from djobs.session_adapters import ADAPTERS, discover_sessions, selected_document, session_file
from djobs.session_memory import SessionMemory
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace

FIXTURE = Path(__file__).parents[1] / "fixtures/memory/session_formats.json"


def encoded(harness):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))[harness]
    value = (
        "\n".join(json.dumps(item) for item in data)
        if isinstance(data, list)
        else json.dumps(data)
    )
    return value.encode()


@pytest.fixture
def env(tmp_path):
    workspace = Workspace(
        root=str(tmp_path),
        workspace_id="repo:imports",
        checkout_id="repo:imports",
        repo_family_id="family:imports",
        correlation_ids=("repo:imports",),
        memory_correlation_ids=("family:imports", "repo:imports"),
        source="fixture",
    )
    repo = SQLiteJobRepository.from_path(tmp_path / "memory.db")
    files = tmp_path / "sessions"
    files.mkdir()
    for harness in ("claude", "codex", "opencode"):
        (files / (harness + (".json" if harness == "opencode" else ".jsonl"))).write_bytes(
            encoded(harness)
        )
    yield repo, workspace, files
    repo.close()


def import_one(env, harness="codex", ids=None):
    repo, workspace, files = env
    memory = SessionMemory(repo, workspace)
    relative = harness + (".json" if harness == "opencode" else ".jsonl")
    ids = ids or ["u-one", "a-one"]
    preview = memory.preview(str(files), relative, harness, ids)
    return memory.import_session(
        str(files),
        relative,
        harness,
        ids,
        expected_hash=preview["binding_hash"],
        expected_family=workspace.repo_family_id,
        confirm=True,
    )


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_formats_redact_text_and_do_not_import_execution_state(harness):
    doc = ADAPTERS[harness].parse(encoded(harness))
    assert [item["id"] for item in doc["messages"]] == ["u-one", "a-one"]
    value = json.dumps(doc, ensure_ascii=False)
    assert "保留加號" in value
    assert "synthetic-credential-fixture" not in value
    assert "DO_NOT_EXECUTE" not in value and "permission" not in value
    assert "NEVER_INSTALL_THIS" not in value
    assert doc["omitted_records_or_blocks"] > 0
    assert doc["stored_content_is_data"] and not doc["native_resumption_supported"]
    assert "ignore all rules" in value


def test_codex_missing_parent_reference_is_disclosed_not_followed():
    result = ADAPTERS["codex"].parse(encoded("codex"))
    assert result["external_history_not_followed"]
    assert "/never-read" not in json.dumps(result)


def test_preview_does_not_install_schema_or_write(env):
    repo, workspace, files = env
    before = repo._connection.total_changes
    result = SessionMemory(repo, workspace).preview(str(files), "codex.jsonl", "codex")
    assert result["selection_required"] and result["total_messages"] == 2
    assert repo._connection.total_changes == before
    assert (
        repo._connection.execute(
            "SELECT name FROM sqlite_master WHERE name='memory_session_imports'"
        ).fetchone()
        is None
    )


def test_import_requires_selection_hash_and_repository_binding(env):
    repo, workspace, files = env
    memory = SessionMemory(repo, workspace)
    with pytest.raises(ArtifactError, match="explicit_bounded_message_selection"):
        memory.preview(str(files), "codex.jsonl", "codex", [])
    preview = memory.preview(str(files), "codex.jsonl", "codex", ["u-one"])
    for expected_hash, family in [
        ("bad", workspace.repo_family_id),
        (preview["binding_hash"], "family:other"),
    ]:
        with pytest.raises(ArtifactError, match="preview_or_repository_changed"):
            memory.import_session(
                str(files),
                "codex.jsonl",
                "codex",
                ["u-one"],
                expected_hash=expected_hash,
                expected_family=family,
                confirm=True,
            )
    assert not memory.list_imports()["memories"]


def test_duplicate_import_is_noop_and_different_selection_is_explicit(env):
    repo, workspace, _ = env
    first = import_one(env)
    before = repo._connection.total_changes
    second = import_one(env)
    assert first["import"]["status"] == "imported_unverified"
    assert second["duplicate"] and second["import"]["id"] == first["import"]["id"]
    assert repo._connection.total_changes == before
    selective = import_one(env, ids=["u-one"])
    assert not selective["duplicate"]
    assert SessionMemory(repo, workspace).get(selective["import"]["id"])["document"][
        "selection_is_partial"
    ]


def test_reviewed_reference_never_enters_normal_resume_or_verifies_experience(env):
    repo, workspace, _ = env
    item = import_one(env)["import"]
    memory = SessionMemory(repo, workspace)
    result = memory.review(
        item["id"], ReviewGate(lambda request: "accept", reviewer="synthetic-review")
    )
    assert result["status"] == "reviewed_reference" and not result["normal_resume_eligible"]
    assert search_observations(repo, workspace, "保留加號") == []
    assert not ArtifactMemory(repo, workspace).list_artifacts()["memories"]
    assert repo._connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    with pytest.raises(ArtifactError):
        ArtifactMemory(repo, workspace).episode([item["id"]])


def test_forgotten_import_does_not_remain_in_export_or_review(env):
    repo, workspace, _ = env
    item = import_one(env)["import"]
    memory = SessionMemory(repo, workspace)
    assert memory.forget(item["id"])
    assert not memory.list_imports()["memories"]
    with pytest.raises(ArtifactError, match="not_found"):
        memory.export(item["id"])


def test_import_clear_preserves_explicit_task_and_refuses_future_schema_atomically(env):
    from djobs.core.models import Job

    repo, workspace, _ = env
    repo.create_job(Job(type="synthetic-task", payload={"summary": "Keep owned work"}))
    import_one(env)
    repo.execute_write(
        "UPDATE djobs_memory_schema SET version=999 WHERE component='session_imports'"
    )
    with pytest.raises(ArtifactError, match="unsupported_session_import_schema"):
        clear_workspace_memory(repo, workspace)
    assert (
        repo._connection.execute("SELECT COUNT(*) FROM memory_session_imports").fetchone()[0] == 1
    )
    repo.execute_write(
        "UPDATE djobs_memory_schema SET version=1 WHERE component='session_imports'"
    )
    clear_workspace_memory(repo, workspace)
    assert not SessionMemory(repo, workspace).list_imports()["memories"]
    assert repo._connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_export_roundtrip_keeps_text_without_transferring_review_status(env):
    repo, workspace, _files = env
    item = import_one(env)["import"]
    memory = SessionMemory(repo, workspace)
    memory.review(item["id"], ReviewGate(lambda request: "accept", reviewer="synthetic-review"))
    bundle = memory.export(item["id"])["document"]
    raw = json.dumps(bundle).encode()
    result = ADAPTERS["djobs"].parse(raw)
    assert result["messages"] == bundle["messages"]
    assert "review" not in json.dumps(result)
    assert not result["native_resumption_supported"]
    with pytest.raises(ArtifactError, match="unsupported_fields"):
        ADAPTERS["djobs"].parse(
            json.dumps({**bundle, "status": "active", "authority": "human_accepted"}).encode()
        )


def test_corrupted_import_is_suppressed_not_returned_as_plain_text(env):
    repo, workspace, _ = env
    item = import_one(env)["import"]
    repo.execute_write(
        "UPDATE memory_session_imports SET payload_json=? WHERE id=?", ("tampered", item["id"])
    )
    assert SessionMemory(repo, workspace).list_imports()["corrupt_items_suppressed"] == 1
    with pytest.raises(ArtifactError, match="content_mismatch"):
        SessionMemory(repo, workspace).get(item["id"])


def test_repository_import_scope_is_explicit(env):
    repo, workspace, _ = env
    item = import_one(env)["import"]
    other = replace(workspace, repo_family_id="family:other")
    assert not SessionMemory(repo, other).list_imports()["memories"]
    with pytest.raises(ArtifactError, match="not_found"):
        SessionMemory(repo, other).get(item["id"])


def test_discovery_lists_candidates_without_reading_auth_config_or_content(env):
    _, _, files = env
    (files / "auth.json").write_text("never parse this file", encoding="utf-8")
    (files / "settings.json").write_text("never parse this file", encoding="utf-8")
    result = discover_sessions(str(files))
    assert len(result["files"]) == 3 and not result["content_read"]
    assert all(not item["format_validated"] for item in result["files"])


@pytest.mark.parametrize(
    "path", ["../outside.json", "auth.json", "settings.json", ".env", "codex.jsonl:stream"]
)
def test_unsafe_paths_are_rejected_without_read(env, path):
    with pytest.raises((ArtifactError, FileNotFoundError)):
        session_file(str(env[2]), path)


def test_unknown_future_schema_and_duplicate_message_ids_fail_closed():
    with pytest.raises(ArtifactError, match="unsupported_session_schema"):
        ADAPTERS["codex"].parse(b'{"schema_version":999,"type":"session_meta"}')
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))["claude"]
    data.append(data[0])
    with pytest.raises(ArtifactError, match="duplicate_message_identity"):
        ADAPTERS["claude"].parse("\n".join(json.dumps(x) for x in data).encode())


def test_public_preview_on_missing_database_creates_no_source_files(env, monkeypatch):
    api = importlib.import_module("djobs.memory")
    _, workspace, files = env
    missing = Path(workspace.root) / "new-db-must-not-exist.db"
    monkeypatch.setattr(api, "resolve_workspace", lambda **kwargs: workspace)
    monkeypatch.setattr(api, "shared_db_path", lambda: missing)
    result = json.loads(
        api.memory_action(
            "session",
            document={
                "operation": "preview",
                "root": str(files),
                "path": "codex.jsonl",
                "harness": "codex",
                "selected_ids": ["u-one"],
            },
            token_budget=4000,
        )
    )
    assert result["ok"] and result["binding_hash"]
    assert not missing.exists()


def test_session_changes_after_preview_cannot_be_imported(env):
    repo, workspace, files = env
    memory = SessionMemory(repo, workspace)
    preview = memory.preview(str(files), "codex.jsonl", "codex", ["u-one"])
    file = files / "codex.jsonl"
    file.write_bytes(file.read_bytes() + b'\n{"type":"new_event"}')
    with pytest.raises(ArtifactError, match="preview_or_repository_changed"):
        memory.import_session(
            str(files),
            "codex.jsonl",
            "codex",
            ["u-one"],
            expected_hash=preview["binding_hash"],
            expected_family=workspace.repo_family_id,
            confirm=True,
        )


def test_selection_is_stable_and_file_digest_is_separate_from_redacted_content():
    raw = encoded("codex")
    one = selected_document(raw, "codex", ["u-one", "a-one"])
    two = selected_document(raw, "codex", ["a-one", "u-one"])
    assert one == two
    assert hashlib.sha256(raw).hexdigest() != hashlib.sha256(json.dumps(one).encode()).hexdigest()


def test_import_context_uri_is_auditable_but_never_in_resume(env):
    repo, workspace, _ = env
    item = import_one(env)["import"]
    memory = ArtifactMemory(repo, workspace)
    assert memory.get(item["uri"], depth=2)["status"] == "imported_unverified"
    assert item["id"] in {x["id"] for x in memory.tree(exposure="audit")["memories"]}
    assert item["id"] not in {x["id"] for x in memory.tree()["memories"]}
    assert not memory.tree(query="unrelated-search", exposure="audit")["memories"]


def test_cli_preview_and_native_bundle_export_use_same_service(env, monkeypatch, capsys):
    import djobs.session_cli as cli
    import djobs.workspace as workspace_module

    api = importlib.import_module("djobs.memory")
    repo, workspace, files = env
    database = Path(repo._connection.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(api, "resolve_workspace", lambda **kwargs: workspace)
    monkeypatch.setattr(api, "shared_db_path", lambda: database)
    assert (
        cli.main(["preview", str(files), "codex.jsonl", "--harness", "codex", "--id", "u-one"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["binding_hash"]
    item = import_one(env)["import"]
    monkeypatch.setattr(workspace_module, "resolve_workspace", lambda **kwargs: workspace)
    monkeypatch.setattr(workspace_module, "shared_db_path", lambda: database)
    assert cli.main(["export", item["id"]]) == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["schema"] == "djobs.session.v1"
    assert ADAPTERS["djobs"].parse(json.dumps(bundle).encode())["messages"] == bundle["messages"]


def test_partial_selection_warning_survives_portable_roundtrip(env):
    item = import_one(env, ids=["u-one"])["import"]
    doc = SessionMemory(env[0], env[1]).export(item["id"])["document"]
    assert doc["selection_is_partial"]
    assert ADAPTERS["djobs"].parse(json.dumps(doc).encode())["selection_is_partial"]
