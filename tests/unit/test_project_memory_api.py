from __future__ import annotations

import types

import djobs
from djobs import project_memory
from djobs.project_memory import ProjectMemory


def test_project_memory_is_exported_as_primary_api() -> None:
    assert djobs.ProjectMemory is ProjectMemory
    assert djobs.sync_workspace is djobs.handoff.sync_workspace
    assert djobs.checkpoint is djobs.handoff.checkpoint
    assert djobs.handoff_task is djobs.handoff.handoff
    assert isinstance(djobs.handoff, types.ModuleType)
    assert djobs.memory_action is not None


def test_open_normalizes_roots_without_accessing_storage() -> None:
    roots = ["repo", "nested"]

    memory = ProjectMemory.open(
        cwd="/workspace",
        roots=roots,
        agent_type="test-agent",
        session_id="session-1",
    )
    roots.append("later")

    assert memory.cwd == "/workspace"
    assert memory.roots == ("repo", "nested")
    assert memory.agent_type == "test-agent"
    assert memory.session_id == "session-1"


def test_sync_workspace_forwards_fixed_context(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        project_memory,
        "_sync_workspace",
        lambda **kwargs: calls.append(kwargs) or "{}",
    )
    memory = ProjectMemory.open(
        cwd="/workspace",
        roots=["repo"],
        agent_type="test-agent",
        session_id="session-1",
    )

    result = memory.sync_workspace(
        query="continue parser",
        max_items=4,
        token_budget=320,
        context_tier="resume",
    )

    assert result == "{}"
    assert calls == [
        {
            "roots": ("repo",),
            "cwd": "/workspace",
            "agent_type": "test-agent",
            "session_id": "session-1",
            "query": "continue parser",
            "max_items": 4,
            "token_budget": 320,
            "context_tier": "resume",
        }
    ]


def test_sync_workspace_defaults_to_compact_resume_context(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        project_memory, "_sync_workspace", lambda **kwargs: calls.append(kwargs) or "{}"
    )

    ProjectMemory.open(cwd="/workspace").sync_workspace()

    assert calls[0]["context_tier"] == "resume"


def test_passive_memory_operations_forward_without_task_ownership(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        project_memory,
        "_memory_action",
        lambda action, **kwargs: calls.append((action, kwargs)) or "{}",
    )
    memory = ProjectMemory.open(cwd="/workspace", agent_type="test-agent", session_id="s")

    assert memory.list_memory(max_items=3, token_budget=250) == "{}"
    assert memory.search_memory("oauth callback", max_items=5) == "{}"
    assert (
        memory.update_memory_status(
            "memory-1",
            "superseded",
            replacement_id="memory-2",
            resolved_by_commit="abc123",
        )
        == "{}"
    )
    assert memory.forget_memory("memory-1") == "{}"
    assert memory.memory_stats() == "{}"
    assert memory.compact_memory(dry_run=True, keep_recent=25) == "{}"
    assert memory.clear_memory(confirm=True) == "{}"

    common = {
        "roots": None,
        "cwd": "/workspace",
        "agent_type": "test-agent",
        "session_id": "s",
    }
    assert calls == [
        ("list", {**common, "max_items": 3, "token_budget": 250}),
        (
            "search",
            {**common, "query": "oauth callback", "max_items": 5, "token_budget": 700},
        ),
        (
            "status",
            {
                **common,
                "memory_id": "memory-1",
                "status": "superseded",
                "replacement_id": "memory-2",
                "resolved_by_commit": "abc123",
            },
        ),
        ("forget", {**common, "memory_id": "memory-1"}),
        ("stats", common),
        (
            "compact",
            {**common, "dry_run": True, "keep_recent": 25, "confirm": False},
        ),
        ("clear", {**common, "confirm": True}),
    ]


def test_explicit_checkpoint_and_handoff_are_separate_methods(monkeypatch) -> None:
    checkpoint_calls = []
    handoff_calls = []
    monkeypatch.setattr(
        project_memory,
        "_checkpoint",
        lambda summary, **kwargs: checkpoint_calls.append((summary, kwargs)) or "checkpoint",
    )
    monkeypatch.setattr(
        project_memory,
        "_handoff",
        lambda task_id, evidence, **kwargs: (
            handoff_calls.append((task_id, evidence, kwargs)) or "handoff"
        ),
    )
    memory = ProjectMemory.open(cwd="/workspace", agent_type="test-agent", session_id="s")

    assert (
        memory.checkpoint(
            "Fix parser",
            path="src/parser.py",
            details="Preserve public API",
            lease_seconds=120,
        )
        == "checkpoint"
    )
    assert memory.handoff("task-1", "Focused tests passed", completed=True) == "handoff"

    common = {
        "roots": None,
        "cwd": "/workspace",
        "agent_type": "test-agent",
        "session_id": "s",
    }
    assert checkpoint_calls == [
        (
            "Fix parser",
            {
                "path": "src/parser.py",
                "details": "Preserve public API",
                **common,
                "lease_seconds": 120,
            },
        )
    ]
    assert handoff_calls == [
        (
            "task-1",
            "Focused tests passed",
            {"completed": True, **common},
        )
    ]
