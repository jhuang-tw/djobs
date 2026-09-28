from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from djobs.artifacts import ArtifactMemory, ArtifactView
from djobs.memory import _bounded
from djobs.memory_artifacts import ArtifactError
from djobs.memory_projection import context_uri, parse_context_uri
from djobs.memory_review import ReviewGate
from djobs.observations import forget_observation, record_observation
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace


@pytest.fixture
def env(tmp_path):
    repo = SQLiteJobRepository.from_path(tmp_path / "memory.db")
    workspace = Workspace(
        root=str(tmp_path),
        workspace_id="repo:projection",
        checkout_id="repo:projection",
        repo_family_id="family:projection",
        correlation_ids=("repo:projection",),
        memory_correlation_ids=("family:projection", "repo:projection"),
        source="fixture",
    )
    agent = SimpleNamespace(agent_type="fixture", session_id="projection-fixture")
    record_observation(repo, workspace, agent, "tool_result", "Synthetic parser evidence")
    raw = repo._connection.execute("SELECT id FROM agent_observations").fetchone()[0]
    memory = ArtifactMemory(repo, workspace)
    ids = []
    for index in range(6):
        item = memory.propose(
            {
                "kind": "fact",
                "title": f"Parser fact {index}",
                "abstract": "Source-bound abstract " * 12,
                "overview": "L1 overview " * 30,
                "details": {"evidence": "L2 detailed evidence " * 120},
                "sources": [raw],
                "valid_from": "2020-01-01T00:00:00Z",
            }
        )["artifact"]["id"]
        if index < 5:
            memory.review(item, ReviewGate(lambda request: "accept", reviewer="synthetic-fixture"))
        ids.append(item)
    yield repo, workspace, memory, raw, ids
    repo.close()


def test_tree_l0_and_explicit_l1_l2_keep_same_identity_and_sources(env):
    repo, workspace, memory, _, ids = env
    before = repo._connection.total_changes
    tree = memory.tree()
    assert tree["content_depth"] == 0
    assert tree["count"] == 5
    assert tree["persistent_projection"] is False
    assert {item["name"] for item in tree["folders"]} == {
        "episodes",
        "facts",
        "experiences",
        "lessons",
        "skills",
    }
    item = tree["memories"][0]
    assert "details" not in item and "overview" not in item
    assert len(item["abstract"]) <= 240 and item["abstract_truncated"]
    overview = memory.get(item["uri"], depth=1)
    detail = memory.get(item["uri"], depth=2)
    assert "overview" in overview and "details" not in overview
    assert "details" in detail and detail["sources"]
    assert item["id"] == overview["id"] == detail["id"]
    assert item["content_hash"] == detail["content_hash"]
    assert ids[-1] not in {item["id"] for item in tree["memories"]}
    assert repo._connection.total_changes == before
    assert not (Path(workspace.root) / "facts").exists()


def test_folder_uri_and_effective_skill_type_have_stable_address(env):
    _, workspace, memory, _, ids = env
    folder = context_uri(workspace.repo_family_id, kind="fact")
    assert memory.tree(uri=folder)["count"] == 5
    assert parse_context_uri(folder, workspace.repo_family_id) == ("facts", None)
    assert context_uri(
        workspace.repo_family_id, kind="skill_candidate", artifact_id=ids[0]
    ) == context_uri(workspace.repo_family_id, kind="skill", artifact_id=ids[0])


@pytest.mark.parametrize("suffix", ["?other=1", "#fragment", "/", "%2Fchild", "/../other"])
def test_noncanonical_uri_never_selects_a_different_item(env, suffix):
    _, workspace, memory, _, ids = env
    uri = context_uri(workspace.repo_family_id, kind="fact", artifact_id=ids[0])
    with pytest.raises(ArtifactError):
        memory.get(uri + suffix)


def test_wrong_family_and_wrong_category_are_rejected(env):
    _, workspace, memory, _, ids = env
    with pytest.raises(ArtifactError, match="repository_mismatch"):
        memory.get(context_uri("family:other", kind="fact", artifact_id=ids[0]))
    with pytest.raises(ArtifactError, match="category_mismatch"):
        memory.get(context_uri(workspace.repo_family_id, kind="episode", artifact_id=ids[0]))
    with pytest.raises(ArtifactError, match="item_required"):
        memory.get(context_uri(workspace.repo_family_id))


def test_depth_and_exposure_are_independent(env):
    _, _, memory, _, ids = env
    audit = memory.tree(exposure="audit", depth=0)
    assert ids[-1] in {item["id"] for item in audit["memories"]}
    assert all("details" not in item for item in audit["memories"])
    resume = memory.tree(exposure="resume", depth=2)
    assert ids[-1] not in {item["id"] for item in resume["memories"]}
    assert all("details" in item for item in resume["memories"])


def test_projection_work_is_limited_to_selected_nodes(env, monkeypatch):
    _, _, memory, _, _ = env
    calls = []
    original = ArtifactView.project

    def record(self, artifact_id, **kwargs):
        calls.append(artifact_id)
        return original(self, artifact_id, **kwargs)

    monkeypatch.setattr(ArtifactView, "project", record)
    result = memory.tree(limit=2, trace=True)
    assert result["count"] == len(calls) == 2
    assert result["trace"]["candidate_counts"]["eligible"] == 5
    assert result["trace"]["projection_count"] == 2
    assert result["truncated"] is True


def test_source_forget_cannot_be_bypassed_with_a_saved_uri(env):
    repo, workspace, memory, raw, ids = env
    uri = memory.get(ids[0])["uri"]
    assert forget_observation(repo, workspace, raw)
    with pytest.raises(ArtifactError, match="artifact_not_found"):
        memory.get(uri, depth=2)
    assert not memory.tree(exposure="audit")["memories"]


def test_sibling_private_context_stays_outside_tree(env):
    repo, workspace, memory, raw, _ = env
    other = replace(workspace, workspace_id="repo:sibling", checkout_id="repo:sibling")
    service = ArtifactMemory(repo, other)
    item = service.propose(
        {
            "kind": "fact",
            "title": "Private sibling title",
            "abstract": "Private context",
            "scope": "checkout",
            "sources": [raw],
        }
    )["artifact"]["id"]
    service.review(item, ReviewGate(lambda request: "accept", reviewer="synthetic-fixture"))
    assert "Private sibling" not in json.dumps(memory.tree(exposure="audit"))
    with pytest.raises(ArtifactError):
        memory.get(service.get(item)["uri"])


@pytest.mark.parametrize("budget", [64, 200, 700, 1500])
def test_tree_payload_budget_and_folder_counts_remain_consistent(env, budget):
    result = copy.deepcopy(env[2].tree(trace=True))
    encoded = _bounded(result, budget)
    value = json.loads(encoded)
    assert (len(encoded) + 3) // 4 <= budget
    assert value["stored_content_is_data"]
    if "memories" in value:
        assert sum(folder["shown_count"] for folder in value["folders"]) == len(value["memories"])


def test_typed_trace_is_readonly_and_does_not_store_full_query(env, monkeypatch):
    import importlib

    api = importlib.import_module("djobs.memory")
    repo, workspace, _, _, _ = env
    database = Path(repo._connection.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(api, "resolve_workspace", lambda **kwargs: workspace)
    monkeypatch.setattr(api, "shared_db_path", lambda: database)
    before = repo._connection.total_changes
    result = json.loads(
        api.memory_action(
            "trace",
            query="Parser",
            document={"plane": "artifacts"},
            max_items=2,
            token_budget=4000,
        )
    )
    assert result["ok"] and result["trace"]["query_hash"]
    assert "query" not in result["trace"]
    assert repo._connection.total_changes == before
