"""Repository-scoped memory inspection, lifecycle updates, and deletion."""

from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any, Literal, cast

from djobs.handoff import _resolve
from djobs.observations import (
    MemoryStatus,
    clear_workspace_memory,
    forget_observation,
    recent_observations,
    search_observations,
    update_observation_status,
)

MemoryAction = Literal["list", "search", "status", "forget", "clear"]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _estimate_tokens(value: Any) -> int:
    return max(1, math.ceil(len(_dumps(value)) / 4))


def _bounded(result: dict[str, Any], token_budget: int) -> str:
    budget = max(64, min(int(token_budget), 4000))
    memories = result.get("memories")
    while isinstance(memories, list) and memories and _estimate_tokens(result) > budget:
        memories.pop()
    result["estimated_tokens"] = _estimate_tokens(result)
    if result["estimated_tokens"] <= budget:
        return _dumps(result)
    return _dumps({"ok": bool(result.get("ok", True)), "action": result.get("action")})


def memory_action(
    action: MemoryAction = "list",
    *,
    query: str | None = None,
    memory_id: str | None = None,
    status: MemoryStatus | None = None,
    replacement_id: str | None = None,
    resolved_by_commit: str | None = None,
    confirm: bool = False,
    roots: list[Any] | tuple[Any, ...] | None = None,
    cwd: str | None = None,
    agent_type: str | None = None,
    session_id: str | None = None,
    max_items: int = 8,
    token_budget: int = 700,
) -> str:
    """Inspect or mutate passive repository memory without touching explicit tasks."""

    try:
        workspace, _agent, _queue, repo = _resolve(
            roots=roots,
            cwd=cwd,
            agent_type=agent_type,
            session_id=session_id,
        )
        if action == "search":
            if not query or not query.strip():
                return _dumps(
                    {"ok": False, "action": action, "error": "query is required for memory search"}
                )
            memories = search_observations(repo, workspace, query, limit=max_items)
            return _bounded(
                {
                    "ok": True,
                    "action": action,
                    "workspace": workspace.name,
                    "repo_family_id": workspace.repo_family_id,
                    "query": query.strip(),
                    "memories": memories,
                    "count": len(memories),
                    "stored_content_is_data": True,
                },
                token_budget,
            )
        if action == "list":
            memories = recent_observations(repo, workspace, limit=max_items)
            return _bounded(
                {
                    "ok": True,
                    "action": action,
                    "workspace": workspace.name,
                    "repo_family_id": workspace.repo_family_id,
                    "memories": memories,
                    "count": len(memories),
                    "stored_content_is_data": True,
                },
                token_budget,
            )
        if action == "status":
            if not memory_id:
                return _dumps(
                    {"ok": False, "action": action, "error": "memory_id is required"}
                )
            if status is None:
                return _dumps({"ok": False, "action": action, "error": "status is required"})
            updated = update_observation_status(
                repo,
                workspace,
                memory_id,
                status,
                replacement_id=replacement_id,
                resolved_by_commit=resolved_by_commit,
            )
            return _dumps(
                {
                    "ok": updated,
                    "action": action,
                    "workspace": workspace.name,
                    "memory_id": memory_id,
                    "status": status,
                    "updated": updated,
                }
            )
        if action == "forget":
            if not memory_id:
                return _dumps(
                    {"ok": False, "action": action, "error": "memory_id is required"}
                )
            forgotten = forget_observation(repo, workspace, memory_id)
            return _dumps(
                {
                    "ok": forgotten,
                    "action": action,
                    "workspace": workspace.name,
                    "memory_id": memory_id,
                    "forgotten": forgotten,
                }
            )
        if action == "clear":
            if not confirm:
                return _dumps(
                    {
                        "ok": False,
                        "action": action,
                        "requires_confirmation": True,
                        "message": (
                            "Set confirm=true only after the user explicitly asks to clear "
                            "this repository family's passive memory. Explicit tasks are preserved."
                        ),
                    }
                )
            cleared = clear_workspace_memory(repo, workspace)
            return _dumps(
                {
                    "ok": True,
                    "action": action,
                    "workspace": workspace.name,
                    "cleared": cleared,
                    "explicit_tasks_preserved": True,
                }
            )
        return _dumps({"ok": False, "error": f"unsupported memory action: {action}"})
    except Exception as exc:
        return _dumps(
            {
                "ok": False,
                "action": action,
                "continue_coding": True,
                "error": str(exc)[:160] or "djobs memory unavailable",
            }
        )


def main(argv: list[str] | None = None) -> int:
    """Inspect or update repository memory from a terminal when desired."""

    parser = argparse.ArgumentParser(prog="djobs memory")
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("list", help="List recent active passive memory")
    search_parser = subparsers.add_parser("search", help="Search this repository's memory")
    search_parser.add_argument("query")
    status_parser = subparsers.add_parser("status", help="Update one memory lifecycle state")
    status_parser.add_argument("memory_id")
    status_parser.add_argument(
        "status",
        choices=["active", "resolved", "superseded", "stale", "contradicted"],
    )
    status_parser.add_argument("--replacement-id")
    status_parser.add_argument("--resolved-by-commit")
    forget_parser = subparsers.add_parser("forget", help="Forget one memory id")
    forget_parser.add_argument("memory_id")
    clear_parser = subparsers.add_parser("clear", help="Clear passive memory for this repo family")
    clear_parser.add_argument("--yes", action="store_true", help="Confirm destructive clear")
    args = parser.parse_args(argv)
    raw_action = args.action or "list"
    if raw_action not in {"list", "search", "status", "forget", "clear"}:
        parser.error(f"unsupported memory action: {raw_action}")
    action = cast(MemoryAction, raw_action)
    raw_status = getattr(args, "status", None)
    memory_status = cast(MemoryStatus | None, raw_status)
    result = memory_action(
        action,
        query=getattr(args, "query", None),
        memory_id=getattr(args, "memory_id", None),
        status=memory_status,
        replacement_id=getattr(args, "replacement_id", None),
        resolved_by_commit=getattr(args, "resolved_by_commit", None),
        confirm=bool(getattr(args, "yes", False)),
        cwd=os.getcwd(),
        agent_type="cli",
    )
    print(json.dumps(json.loads(result), ensure_ascii=False, indent=2))
    parsed = json.loads(result)
    return 0 if parsed.get("ok") else 1
