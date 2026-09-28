"""Repository-scoped memory inspection, lifecycle updates, and deletion."""

from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any, Literal, cast

from djobs.contract_repository import _connect
from djobs.embedding import EmbeddingSession, UnavailableEmbeddingProvider
from djobs.observations import (
    MemoryStatus,
    clear_workspace_memory,
    compact_workspace_memory,
    forget_observation,
    recent_observations,
    update_observation_status,
    workspace_memory_stats,
)
from djobs.privacy import redact_text
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_workspace, shared_db_path

MemoryAction = Literal[
    "list", "search", "status", "forget", "clear", "stats", "compact", "trace", "reindex"
]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _estimate_tokens(value: Any) -> int:
    return max(1, math.ceil(len(_dumps(value)) / 4))


def _bounded(result: dict[str, Any], token_budget: int) -> str:
    """Bound the final serialized payload, including its own estimate/authority flag."""

    budget = max(64, min(int(token_budget), 4000))
    result["stored_content_is_data"] = True
    original_count = len(result.get("memories", []))
    result["truncated"] = False
    result["estimated_tokens"] = 0

    def refresh() -> str:
        for _ in range(5):
            estimate = _estimate_tokens(result)
            if estimate == result["estimated_tokens"]:
                break
            result["estimated_tokens"] = estimate
        return _dumps(result)

    encoded = refresh()
    memories = result.get("memories")
    while isinstance(memories, list) and memories and math.ceil(len(encoded) / 4) > budget:
        memories.pop()
        result["count"] = len(memories)
        result["truncated"] = True
        result["critical_evidence_omitted"] = True
        if isinstance(result.get("trace"), dict):
            result["trace"]["selected_ids"] = [item["id"] for item in memories]
        encoded = refresh()
    if original_count and not result.get("memories"):
        result["critical_evidence_omitted"] = True
        encoded = refresh()
    if math.ceil(len(encoded) / 4) > budget and "trace" in result:
        result.pop("trace")
        result["trace_omitted"] = True
        result["truncated"] = True
        encoded = refresh()
    if math.ceil(len(encoded) / 4) <= budget:
        return encoded
    result = {
        "ok": bool(result.get("ok", True)),
        "action": result.get("action"),
        "stored_content_is_data": True,
        "truncated": True,
        "critical_evidence_omitted": True,
        "estimated_tokens": 0,
    }
    return refresh()


def memory_action(
    action: MemoryAction = "list",
    *,
    query: str | None = None,
    memory_id: str | None = None,
    status: MemoryStatus | None = None,
    replacement_id: str | None = None,
    resolved_by_commit: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
    keep_recent: int = 100,
    roots: list[Any] | tuple[Any, ...] | None = None,
    cwd: str | None = None,
    agent_type: str | None = None,
    session_id: str | None = None,
    max_items: int = 8,
    token_budget: int = 700,
    embedding: EmbeddingSession | None = None,
    explain: bool = False,
) -> str:
    """Inspect or mutate passive repository memory without touching explicit tasks."""

    del agent_type, session_id  # Memory access does not register an owner or agent.
    repo = None
    try:
        if action not in {
            "list",
            "search",
            "trace",
            "status",
            "forget",
            "clear",
            "stats",
            "compact",
            "reindex",
        }:
            return _dumps({"ok": False, "error": "unsupported memory action"})
        if action == "reindex" and (not confirm or embedding is None):
            return _dumps(
                {
                    "ok": False,
                    "action": action,
                    "requires_confirmation": not confirm,
                    "error": "reindex requires an explicit provider and confirmation",
                }
            )
        if (action == "clear" or (action == "compact" and not dry_run)) and not confirm:
            return _dumps(
                {
                    "ok": False,
                    "action": action,
                    "requires_confirmation": True,
                    "message": "Explicit confirmation is required before destructive maintenance.",
                }
            )
        workspace = resolve_workspace(roots=roots, cwd=cwd)
        path = shared_db_path()
        if action in {"list", "search", "trace", "stats"} or (action == "compact" and dry_run):
            connection = _connect(path)
            if connection is None:
                return _bounded(
                    {
                        "ok": True,
                        "action": action,
                        "memories": [],
                        "count": 0,
                        "memory_store_status": "not_initialized",
                    },
                    token_budget,
                )
            repo = SQLiteJobRepository(connection)
        else:
            repo = SQLiteJobRepository.from_path(path)
        if action == "reindex":
            from djobs.retrieval import reindex_memory

            assert embedding is not None
            return _bounded(
                {"action": action, **reindex_memory(repo, workspace, embedding)}, token_budget
            )
        if action in {"search", "trace"}:
            if not query or not query.strip():
                return _dumps(
                    {"ok": False, "action": action, "error": "query is required for memory search"}
                )
            from djobs.retrieval import retrieve_memory

            retrieval = retrieve_memory(
                repo,
                workspace,
                query,
                limit=max_items,
                embedding=embedding,
                explain=explain or action == "trace",
            )
            memories = retrieval.items
            extra = {}
            if action == "trace":
                extra["trace"] = retrieval.trace
            if embedding is not None:
                extra["semantic_index_status"] = retrieval.trace["semantic_index_status"]
                extra["fallback_reason"] = retrieval.trace["fallback_reason"]
            return _bounded(
                {
                    "ok": True,
                    "action": action,
                    "workspace": workspace.name,
                    "repo_family_id": workspace.repo_family_id,
                    "query": redact_text(query.strip()),
                    **extra,
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
                return _dumps({"ok": False, "action": action, "error": "memory_id is required"})
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
                return _dumps({"ok": False, "action": action, "error": "memory_id is required"})
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
        if action == "stats":
            stats = workspace_memory_stats(repo, workspace)
            return _dumps(
                {
                    "ok": True,
                    "action": action,
                    "workspace": workspace.name,
                    "repo_family_id": workspace.repo_family_id,
                    **stats,
                }
            )
        if action == "compact":
            if not dry_run and not confirm:
                return _dumps(
                    {
                        "ok": False,
                        "action": action,
                        "requires_confirmation": True,
                        "message": (
                            "Set confirm=true to delete duplicate and inactive passive memory. "
                            "Explicit tasks are always preserved."
                        ),
                    }
                )
            result = compact_workspace_memory(
                repo,
                workspace,
                keep_recent=keep_recent,
                dry_run=dry_run,
            )
            return _dumps(
                {
                    "ok": True,
                    "action": action,
                    "workspace": workspace.name,
                    "repo_family_id": workspace.repo_family_id,
                    **result,
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
                            "this repository family's passive memory. "
                            "Explicit tasks are preserved."
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
    except Exception:
        # Reads must not turn a provider/storage error into a hidden diagnostic DB write.
        return _bounded(
            {
                "ok": False,
                "action": action,
                "continue_coding": True,
                "error": "memory_unavailable",
            },
            token_budget,
        )
    finally:
        if repo is not None:
            repo.close()


def main(argv: list[str] | None = None) -> int:
    """Inspect or update repository memory from a terminal when desired."""

    parser = argparse.ArgumentParser(prog="djobs memory")
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("list", help="List recent active passive memory")
    search_parser = subparsers.add_parser("search", help="Search this repository's memory")
    search_parser.add_argument("query")
    search_parser.add_argument("--explain", action="store_true")
    search_parser.add_argument("--model-dir", help="Explicit local E5 directory; no download")
    trace_parser = subparsers.add_parser(
        "trace", help="Explain one read without persisting the query"
    )
    trace_parser.add_argument("query")
    trace_parser.add_argument("--model-dir")
    reindex_parser = subparsers.add_parser(
        "reindex", help="Explicit bounded local index maintenance"
    )
    reindex_parser.add_argument("--model-dir", required=True)
    reindex_parser.add_argument("--yes", action="store_true")
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
    subparsers.add_parser("stats", help="Show passive-memory retention statistics")
    compact_parser = subparsers.add_parser(
        "compact", help="Remove duplicate and inactive passive memory"
    )
    compact_parser.add_argument(
        "--dry-run", action="store_true", help="Preview removals without deleting rows"
    )
    compact_parser.add_argument("--keep-recent", type=int, default=100)
    compact_parser.add_argument("--yes", action="store_true", help="Confirm compaction")
    clear_parser = subparsers.add_parser("clear", help="Clear passive memory for this repo family")
    clear_parser.add_argument("--yes", action="store_true", help="Confirm destructive clear")
    args = parser.parse_args(argv)
    raw_action = args.action or "list"
    if raw_action not in {
        "list",
        "search",
        "status",
        "forget",
        "clear",
        "stats",
        "compact",
        "trace",
        "reindex",
    }:
        parser.error(f"unsupported memory action: {raw_action}")
    action = cast(MemoryAction, raw_action)
    raw_status = getattr(args, "status", None)
    memory_status = cast(MemoryStatus | None, raw_status)
    embedding = None
    if getattr(args, "model_dir", None):
        try:
            from djobs.local_embedding import LocalE5Provider

            embedding = EmbeddingSession(LocalE5Provider(args.model_dir))
        except Exception:
            embedding = EmbeddingSession(UnavailableEmbeddingProvider())
    result = memory_action(
        action,
        embedding=embedding,
        explain=bool(getattr(args, "explain", False)),
        query=getattr(args, "query", None),
        memory_id=getattr(args, "memory_id", None),
        status=memory_status,
        replacement_id=getattr(args, "replacement_id", None),
        resolved_by_commit=getattr(args, "resolved_by_commit", None),
        confirm=bool(getattr(args, "yes", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
        keep_recent=int(getattr(args, "keep_recent", 100)),
        cwd=os.getcwd(),
        agent_type="cli",
    )
    print(json.dumps(json.loads(result), ensure_ascii=True, indent=2))
    parsed = json.loads(result)
    return 0 if parsed.get("ok") else 1
