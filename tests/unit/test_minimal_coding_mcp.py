"""Registry-level guards for the default coding MCP tool footprint."""

from __future__ import annotations

import asyncio
import json

from djobs import coding_mcp, delta_mcp

_MINIMAL = {"sync_workspace", "memory", "checkpoint", "handoff", "resume_delta"}
_ADVANCED = {
    "claim_task",
    "heartbeat_task",
    "release_task",
    "register_agent",
    "agent_heartbeat",
    "list_agents",
    "audit_log",
    "health",
}


def _tools(server):
    return asyncio.run(server.list_tools())


def _names(server) -> set[str]:
    return {tool.name for tool in _tools(server)}


def _schema_chars(server) -> int:
    payload = []
    for tool in _tools(server):
        if hasattr(tool, "model_dump"):
            payload.append(tool.model_dump(mode="json"))
        else:
            payload.append(tool.dict())
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def test_default_registry_is_exactly_the_high_level_coding_tools() -> None:
    assert _names(coding_mcp._server) == _MINIMAL


def test_advanced_queue_schemas_and_legacy_tools_are_opt_in() -> None:
    full = _names(delta_mcp._server)
    assert "resume_delta" in full
    assert full >= _ADVANCED
    assert _ADVANCED.isdisjoint(_names(coding_mcp._server))


def test_default_tool_schema_payload_is_materially_smaller() -> None:
    minimal_chars = _schema_chars(coding_mcp._server)
    full_chars = _schema_chars(delta_mcp._server)
    assert minimal_chars < full_chars * 0.55
