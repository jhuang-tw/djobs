from __future__ import annotations

from types import SimpleNamespace

from djobs import mcp_adoption


def test_remember_current_request_is_passive_bounded_and_scoped(monkeypatch) -> None:
    calls: list[object] = []
    workspace = SimpleNamespace(name="repo")
    agent = SimpleNamespace(agent_id="mcp:session")
    repo = object()

    monkeypatch.setattr(mcp_adoption, "automatic_memory_paused", lambda: False)
    monkeypatch.setattr(
        mcp_adoption,
        "_resolve",
        lambda **kwargs: calls.append(("resolve", kwargs)) or (workspace, agent, object(), repo),
    )
    monkeypatch.setattr(
        mcp_adoption,
        "record_unique_session_observation",
        lambda *args, **kwargs: calls.append(("record", args, kwargs)),
    )

    mcp_adoption.remember_current_request(
        "  Fix the OAuth callback without changing the API.  ",
        roots=["repo-root"],
        cwd=None,
        agent_type=None,
    )

    assert calls[0] == (
        "resolve",
        {
            "roots": ["repo-root"],
            "cwd": None,
            "agent_type": "mcp",
            "session_id": None,
        },
    )
    assert calls[1][0] == "record"
    assert calls[1][1][3] == "user_intent"
    assert calls[1][1][4] == "Fix the OAuth callback without changing the API."
    assert calls[1][2]["metadata"] == {"source": "mcp_sync", "stored_as_data": True}


def test_agent_memory_reuses_resume_events_and_stays_passive(monkeypatch) -> None:
    calls: list[object] = []
    workspace = SimpleNamespace(name="repo")
    agent = SimpleNamespace(agent_id="mcp:session")
    repo = object()

    monkeypatch.setattr(mcp_adoption, "automatic_memory_paused", lambda: False)
    monkeypatch.setattr(
        mcp_adoption,
        "_resolve",
        lambda **kwargs: calls.append(("resolve", kwargs)) or (workspace, agent, object(), repo),
    )

    def record(*args, **kwargs):
        calls.append(("record", args, kwargs))
        return True

    monkeypatch.setattr(mcp_adoption, "record_unique_session_observation", record)

    assert mcp_adoption.remember_agent_memory(
        "OAuth callback integration still fails.",
        kind="failure",
        roots=["repo-root"],
        cwd=None,
        agent_type=None,
    )

    assert calls[0][1] == {
        "roots": ["repo-root"],
        "cwd": None,
        "agent_type": "mcp",
        "session_id": None,
    }
    assert calls[1][1][3] == "tool_failure"
    assert calls[1][1][4] == "OAuth callback integration still fails."
    assert calls[1][2]["metadata"] == {
        "source": "mcp_memory",
        "note_kind": "failure",
        "stored_as_data": True,
    }


def test_no_memory_marker_skips_generic_mcp_capture(monkeypatch) -> None:
    monkeypatch.setattr(mcp_adoption, "automatic_memory_paused", lambda: False)
    monkeypatch.setattr(
        mcp_adoption,
        "_resolve",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    mcp_adoption.remember_current_request(
        "[djobs:no-memory] inspect this repository",
        roots=["repo-root"],
        cwd=None,
        agent_type="unknown",
    )


def test_paused_memory_skips_generic_mcp_capture(monkeypatch) -> None:
    monkeypatch.setattr(mcp_adoption, "automatic_memory_paused", lambda: True)
    monkeypatch.setattr(
        mcp_adoption,
        "_resolve",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    mcp_adoption.remember_current_request(
        "continue parser",
        roots=["repo-root"],
        cwd=None,
        agent_type="unknown",
    )

    assert not mcp_adoption.remember_agent_memory(
        "important failure",
        kind="failure",
        roots=["repo-root"],
        cwd=None,
        agent_type="unknown",
    )
