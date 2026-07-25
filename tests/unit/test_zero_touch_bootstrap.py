"""Zero-touch first-call setup must be invisible, idempotent, and fail-open."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from djobs import zero_touch


def _context(client_name: str):
    client_info = SimpleNamespace(name=client_name, title=None)
    params = SimpleNamespace(clientInfo=client_info)
    session = SimpleNamespace(client_params=params)
    return SimpleNamespace(session=session, client_id=None)


def test_detect_host_from_mcp_client_info(monkeypatch) -> None:
    monkeypatch.delenv("DJOBS_AGENT_TYPE", raising=False)
    monkeypatch.delenv("MCP_CLIENT_NAME", raising=False)

    assert zero_touch.detect_host(_context("Visual Studio Code")) == "copilot"
    assert zero_touch.detect_host(_context("Claude Code")) == "claude"
    assert zero_touch.detect_host(_context("OpenAI Codex")) == "codex"


def test_explicit_agent_type_wins(monkeypatch) -> None:
    monkeypatch.setenv("DJOBS_AGENT_TYPE", "gemini")
    assert zero_touch.detect_host(_context("Visual Studio Code")) == "gemini"


def test_first_call_creates_memory_and_installs_only_detected_host(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / ".djobs" / "global.db"
    calls: list[object] = []
    monkeypatch.setenv("DJOBS_DB", str(database))
    monkeypatch.setenv("DJOBS_AGENT_TYPE", "copilot")
    monkeypatch.setattr(zero_touch, "ensure_shared_queue", lambda: calls.append("queue"))

    def install(host, db, *, home, mode):
        calls.append((host, db, home, mode))
        return {"status": "configured"}

    monkeypatch.setattr(zero_touch, "install_host_hooks", install)
    zero_touch.reset_bootstrap_state()

    first = zero_touch.bootstrap_first_call(home=tmp_path)
    second = zero_touch.bootstrap_first_call(home=tmp_path)

    assert first.status == "ready"
    assert first.host == "copilot"
    assert first.hooks == "configured"
    assert second == first
    assert calls == ["queue", ("copilot", database, tmp_path, "smart")]


def test_unknown_client_still_initializes_memory_without_guessing_hooks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "memory.db"))
    monkeypatch.delenv("DJOBS_AGENT_TYPE", raising=False)
    monkeypatch.delenv("MCP_CLIENT_NAME", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(zero_touch, "ensure_shared_queue", lambda: calls.append("queue"))
    monkeypatch.setattr(
        zero_touch,
        "install_host_hooks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not guess host")),
    )
    zero_touch.reset_bootstrap_state()

    result = zero_touch.bootstrap_first_call(_context("Unknown Vibe Client"), home=tmp_path)

    assert result.status == "ready"
    assert result.host is None
    assert result.hooks is None
    assert calls == ["queue"]


def test_bootstrap_failure_never_blocks_the_tool_flow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "memory.db"))
    monkeypatch.setenv("DJOBS_AGENT_TYPE", "claude")
    monkeypatch.setattr(
        zero_touch,
        "ensure_shared_queue",
        lambda: (_ for _ in ()).throw(OSError("read-only home")),
    )
    zero_touch.reset_bootstrap_state()

    result = zero_touch.bootstrap_first_call(home=tmp_path)

    assert result.status == "degraded"
    assert result.error == "read-only home"


def test_minimal_sync_tool_bootstraps_before_reading_workspace(monkeypatch) -> None:
    import asyncio

    from djobs import coding_mcp

    order: list[str] = []

    class Session:
        client_params = SimpleNamespace(
            clientInfo=SimpleNamespace(name="Visual Studio Code", title=None)
        )

        async def list_roots(self):
            order.append("roots")
            return SimpleNamespace(roots=[])

    context = SimpleNamespace(session=Session(), request_context=None, client_id=None)
    monkeypatch.setattr(
        coding_mcp,
        "bootstrap_first_call",
        lambda _context: (
            order.append("bootstrap")
            or zero_touch.BootstrapResult("ready", "copilot", "/tmp/memory.db")
        ),
    )
    captured: dict[str, object] = {}

    def sync(**kwargs):
        captured.update(kwargs)
        order.append("sync")
        return "{}"

    monkeypatch.setattr(coding_mcp, "_sync_workspace", sync)

    assert asyncio.run(coding_mcp.sync_workspace(context)) == "{}"
    assert order == ["bootstrap", "roots", "sync"]
    assert captured["agent_type"] == "copilot"


def test_paused_bootstrap_is_side_effect_free(monkeypatch, tmp_path: Path) -> None:
    from djobs.core.pause import set_paused

    database = tmp_path / "memory.db"
    monkeypatch.setenv("DJOBS_DB", str(database))
    monkeypatch.setenv("DJOBS_AGENT_TYPE", "copilot")
    set_paused(database, True)
    calls: list[str] = []
    monkeypatch.setattr(zero_touch, "ensure_shared_queue", lambda: calls.append("queue"))
    monkeypatch.setattr(
        zero_touch,
        "install_host_hooks",
        lambda *_args, **_kwargs: calls.append("hooks"),
    )
    zero_touch.reset_bootstrap_state()

    result = zero_touch.bootstrap_first_call(home=tmp_path)

    assert result.status == "paused"
    assert calls == []


def test_minimal_sync_tool_reports_pause_before_bootstrap(monkeypatch, tmp_path: Path) -> None:
    import asyncio
    import json

    from djobs import coding_mcp
    from djobs.core.pause import set_paused

    database = tmp_path / "memory.db"
    monkeypatch.setenv("DJOBS_DB", str(database))
    set_paused(database, True)
    monkeypatch.setattr(
        coding_mcp,
        "bootstrap_first_call",
        lambda _context: (_ for _ in ()).throw(AssertionError("must not bootstrap while paused")),
    )

    result = json.loads(asyncio.run(coding_mcp.sync_workspace(None, query="continue")))

    assert result == {
        "ok": True,
        "paused": True,
        "memory_suppressed": True,
        "continue_coding": True,
        "message": "djobs is paused; automatic recovery was skipped.",
    }
