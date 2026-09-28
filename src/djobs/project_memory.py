"""Stable Python facade for repository-scoped djobs memory.

The facade keeps repository and agent context in one object while delegating to
existing fail-open JSON APIs. Explicit checkpoint ownership remains opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from djobs.embedding import EmbeddingSession
from djobs.handoff import checkpoint as _checkpoint
from djobs.handoff import handoff as _handoff
from djobs.handoff import sync_workspace as _sync_workspace
from djobs.memory import MemoryAction
from djobs.memory import memory_action as _memory_action
from djobs.memory_review import ReviewGate
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
    embedding: EmbeddingSession | None = field(default=None, repr=False, compare=False)

    @classmethod
    def open(
        cls,
        *,
        cwd: str | None = None,
        roots: list[Any] | tuple[Any, ...] | None = None,
        agent_type: str | None = None,
        session_id: str | None = None,
        embedding: EmbeddingSession | None = None,
    ) -> ProjectMemory:
        """Create a facade without touching storage or claiming work."""

        normalized_roots = tuple(roots) if roots is not None else None
        return cls(
            cwd=cwd,
            roots=normalized_roots,
            embedding=embedding,
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
            **({"embedding": self.embedding} if self.embedding is not None else {}),
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
        explain: bool = False,
    ) -> str:
        """Search passive memory using deterministic local ranking."""

        return self._memory(
            "search",
            query=query,
            max_items=max_items,
            token_budget=token_budget,
            **({"explain": True} if explain else {}),
        )

    def trace_memory(self, query: str, *, token_budget: int = 2000) -> str:
        """Read an explained retrieval trace without persisting query text."""
        return self._memory("trace", query=query, token_budget=token_budget)

    def reindex_memory(self, *, confirm: bool = False) -> str:
        """Explicitly rebuild the configured derived index; never activate memory."""
        return self._memory("reindex", confirm=confirm)

    def propose_fact(
        self,
        *,
        title: str,
        abstract: str,
        sources: list[Any],
        overview: str = "",
        details: dict[str, Any] | None = None,
        valid_from: str | None = None,
        scope: str = "repository_family",
    ) -> str:
        """Create a source-bound candidate, never an accepted fact."""
        return self._memory(
            "propose",
            document={
                "kind": "fact",
                "title": title,
                "abstract": abstract,
                "sources": sources,
                "overview": overview,
                "details": details or {},
                "valid_from": valid_from,
                "scope": scope,
            },
        )

    def facts(
        self,
        query: str = "",
        *,
        at: str | None = None,
        exposure: str = "resume",
        depth: int = 1,
        max_items: int = 8,
        token_budget: int = 700,
    ) -> str:
        """Read current or historical accepted facts with explicit ambiguity."""
        return self._memory(
            "facts",
            query=query,
            document={"at": at, "depth": depth, "exposure": exposure},
            max_items=max_items,
            token_budget=token_budget,
        )

    def memory_candidates(self, *, token_budget: int = 700) -> str:
        return self._memory("candidates", token_budget=token_budget)

    def get_memory(self, memory_id: str, *, depth: int = 1, token_budget: int = 700) -> str:
        return self._memory(
            "get", memory_id=memory_id, document={"depth": depth}, token_budget=token_budget
        )

    def review_memory(
        self, memory_id: str, *, gate: ReviewGate | None = None, token_budget: int = 2000
    ) -> str:
        """Preview by default; only a trusted product's human review gate may accept."""
        return self._memory(
            "review", memory_id=memory_id, review_gate=gate, token_budget=token_budget
        )

    def relate_facts(
        self,
        source_id: str,
        target_id: str,
        kind: str,
        *,
        at: str | None = None,
        gate: ReviewGate | None = None,
        token_budget: int = 2000,
    ) -> str:
        return self._memory(
            "relate",
            document={"source_id": source_id, "target_id": target_id, "kind": kind, "at": at},
            review_gate=gate,
            token_budget=token_budget,
        )

    def record_episode(self, sources: list[str], *, title: str = "Observed coding episode") -> str:
        return self._memory("episode", document={"sources": sources, "title": title})

    def memory_tree(
        self,
        *,
        uri: str | None = None,
        query: str = "",
        depth: int = 0,
        exposure: str = "resume",
        token_budget: int = 1500,
    ) -> str:
        return self._memory(
            "tree",
            query=query,
            token_budget=token_budget,
            document={"uri": uri, "depth": depth, "exposure": exposure},
        )

    def trace_artifacts(self, query: str, *, token_budget: int = 2000) -> str:
        return self._memory(
            "trace", query=query, token_budget=token_budget, document={"plane": "artifacts"}
        )

    def verify_experience(
        self,
        document: dict[str, Any],
        *,
        gate: ReviewGate | None = None,
        token_budget: int = 3000,
    ) -> str:
        """Preview only unless the trusted product obtains explicit outcome verification."""
        return self._memory(
            "experience", document=document, review_gate=gate, token_budget=token_budget
        )

    def propose_lesson(self, document: dict[str, Any]) -> str:
        return self._memory("propose", document={**document, "kind": "lesson"})

    def propose_skill(self, document: dict[str, Any]) -> str:
        return self._memory("propose", document={**document, "kind": "skill_candidate"})

    def active_skills(self, *, token_budget: int = 1000) -> str:
        return self._memory("facts", document={"kind": "skill"}, token_budget=token_budget)

    def export_skill(
        self,
        memory_id: str,
        destination: str,
        *,
        gate: ReviewGate | None = None,
        token_budget: int = 3000,
    ) -> str:
        return self._memory(
            "export",
            memory_id=memory_id,
            document={"destination": destination},
            review_gate=gate,
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
        if self.embedding is not None:
            kwargs["embedding"] = self.embedding
        return _memory_action(
            action,
            roots=self.roots,
            cwd=self.cwd,
            agent_type=self.agent_type,
            session_id=self.session_id,
            **kwargs,
        )
