"""Stable Python facade for repository-scoped djobs memory.

The facade keeps repository and agent context in one object while delegating to
existing fail-open JSON APIs. Explicit checkpoint ownership remains opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from djobs.handoff import checkpoint as _checkpoint
from djobs.handoff import handoff as _handoff
from djobs.handoff import sync_workspace as _sync_workspace
from djobs.memory import MemoryAction
from djobs.memory import memory_action as _memory_action
from djobs.observations import MemoryStatus


@dataclass(frozen=True, slots=True)
class ProjectMemory:
    """Repository-scoped memory client with optional fixed agent context.

    Methods return the same compact JSON strings as the CLI and MCP-facing
    functions. Reading passive memory never claims work; :meth:`checkpoint`
    is the explicit ownership boundary.
    """

    cwd: str | None = None
    roots: tuple[Any, ...] | None = None
    agent_type: str | None = None
    session_id: str | None = None

    @classmethod
    def open(
        cls,
        *,
        cwd: str | None = None,
        roots: list[Any] | tuple[Any, ...] | None = None,
        agent_type: str | None = None,
        session_id: str | None = None,
    ) -> ProjectMemory:
        """Create a facade without touching storage or claiming work."""

        normalized_roots = tuple(roots) if roots is not None else None
        return cls(
            cwd=cwd,
            roots=normalized_roots,
            agent_type=agent_type,
            session_id=session_id,
        )

    def sync_workspace(
        self,
        *,
        query: str | None = None,
        max_items: int = 6,
        token_budget: int = 500,
        context_tier: str = "resume",
    ) -> str:
        """Read bounded continuation context without claiming a task."""

        return _sync_workspace(
            roots=self.roots,
            cwd=self.cwd,
            agent_type=self.agent_type,
            session_id=self.session_id,
            query=query,
            max_items=max_items,
            token_budget=token_budget,
            context_tier=context_tier,
        )

    def list_memory(self, *, max_items: int = 8, token_budget: int = 700) -> str:
        """List recent active passive memory for this repository family."""

        return self._memory("list", max_items=max_items, token_budget=token_budget)

    def search_memory(
        self,
        query: str,
        *,
        max_items: int = 8,
        token_budget: int = 700,
    ) -> str:
        """Search passive memory using deterministic local ranking."""

        return self._memory(
            "search",
            query=query,
            max_items=max_items,
            token_budget=token_budget,
        )

    def update_memory_status(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        replacement_id: str | None = None,
        resolved_by_commit: str | None = None,
    ) -> str:
        """Update one passive memory lifecycle state."""

        return self._memory(
            "status",
            memory_id=memory_id,
            status=status,
            replacement_id=replacement_id,
            resolved_by_commit=resolved_by_commit,
        )

    def forget_memory(self, memory_id: str) -> str:
        """Delete one passive memory item without touching explicit tasks."""

        return self._memory("forget", memory_id=memory_id)

    def memory_stats(self) -> str:
        """Return passive-memory retention counts without reading explicit tasks."""

        return self._memory("stats")

    def compact_memory(
        self,
        *,
        dry_run: bool = True,
        keep_recent: int = 100,
        confirm: bool = False,
    ) -> str:
        """Preview or apply bounded passive-memory compaction."""

        return self._memory(
            "compact",
            dry_run=dry_run,
            keep_recent=keep_recent,
            confirm=confirm,
        )

    def clear_memory(self, *, confirm: bool = False) -> str:
        """Clear passive repository-family memory after explicit confirmation."""

        return self._memory("clear", confirm=confirm)

    def checkpoint(
        self,
        summary: str,
        *,
        path: str | None = None,
        details: str | None = None,
        lease_seconds: int = 600,
    ) -> str:
        """Explicitly create or resume and claim one repository task."""

        return _checkpoint(
            summary,
            path=path,
            details=details,
            roots=self.roots,
            cwd=self.cwd,
            agent_type=self.agent_type,
            session_id=self.session_id,
            lease_seconds=lease_seconds,
        )

    def handoff(self, task_id: str, evidence: str, *, completed: bool = False) -> str:
        """Release or complete an explicitly owned task with bounded evidence."""

        return _handoff(
            task_id,
            evidence,
            completed=completed,
            roots=self.roots,
            cwd=self.cwd,
            agent_type=self.agent_type,
            session_id=self.session_id,
        )

    def _memory(self, action: MemoryAction, **kwargs: Any) -> str:
        return _memory_action(
            action,
            roots=self.roots,
            cwd=self.cwd,
            agent_type=self.agent_type,
            session_id=self.session_id,
            **kwargs,
        )
