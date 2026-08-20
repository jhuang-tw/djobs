from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from djobs import coding_mcp, zero_touch


def _context():
    class Session:
        client_params = SimpleNamespace(
            clientInfo=SimpleNamespace(name="Unknown MCP Client", title=None)
        )

        async def list_roots(self):
            return SimpleNamespace(roots=["repo-root"])

    return SimpleNamespace(session=Session(), request_context=None, client_id=None)


def test_memory_remember_routes_significant_fact_without_claiming(monkeypatch) -> None:
    context = _context()
    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(
        coding_mcp,
        "bootstrap_first_call",
        lambda _context: zero_touch.BootstrapResult("ready", None, "/tmp/memory.db"),
    )
    monkeypatch.setattr(
        coding_mcp,
        "remember_agent_memory",
        lambda summary, **kwargs: calls.append((summary, kwargs)) or True,
    )

    result = json.loads(
        asyncio.run(
            coding_mcp.memory(
                context,
                action="remember",
                kind="failure",
                summary="OAuth callback integration failed on '+'.",
            )
        )
    )

    assert result == {
        "ok": True,
        "action": "remember",
        "remembered": True,
        "kind": "failure",
    }
    assert calls == [
        (
            "OAuth callback integration failed on '+'.",
            {
                "kind": "failure",
                "roots": ["repo-root"],
                "cwd": None,
                "agent_type": None,
            },
        )
    ]


def test_memory_remember_requires_summary(monkeypatch) -> None:
    context = _context()
    monkeypatch.setattr(
        coding_mcp,
        "bootstrap_first_call",
        lambda _context: zero_touch.BootstrapResult("ready", None, "/tmp/memory.db"),
    )
    monkeypatch.setattr(
        coding_mcp,
        "remember_agent_memory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not record")),
    )

    result = json.loads(asyncio.run(coding_mcp.memory(context, action="remember")))

    assert result["ok"] is False
    assert result["continue_coding"] is True
    assert "summary is required" in result["error"]
