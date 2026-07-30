"""Storage boundary for repository-scoped task views and explicit claims."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HandoffRepository(Protocol):
    def configure_runtime(self) -> None: ...
    def recover_null_retries(self, *, updated_at: datetime) -> int: ...

    def task_rows(
        self,
        *,
        correlation_ids: tuple[str, ...],
        statuses: tuple[str, ...],
        limit: int,
        evidence_events: tuple[str, ...],
    ) -> list[dict[str, Any]]: ...

    def completed_rows(
        self, *, correlation_ids: tuple[str, ...], limit: int
    ) -> list[dict[str, Any]]: ...

    def claim_exact(
        self,
        *,
        task_id: str,
        agent_id: str,
        agent_type: str,
        lease_seconds: int,
    ) -> tuple[str, dict[str, Any] | None]: ...

    def scoped_job(
        self, *, task_id: str, correlation_ids: tuple[str, ...]
    ) -> dict[str, Any] | None: ...

    def owned_rows(
        self,
        *,
        correlation_ids: tuple[str, ...],
        job_types: tuple[str, ...],
        agent_id: str,
    ) -> list[dict[str, Any]]: ...


# Compatibility name for internal imports written before the boundary was named
# after its explicit checkpoint/handoff responsibility.
WorkspaceRepository = HandoffRepository


@dataclass(slots=True)
class SQLiteWorkspaceRepository:
    repo: Any

    @staticmethod
    def _placeholders(values: tuple[Any, ...]) -> str:
        return ",".join("?" for _ in values)

    def configure_runtime(self) -> None:
        self.repo.configure_concurrency()

    def recover_null_retries(self, *, updated_at: datetime) -> int:
        return self.repo.execute_write(
            """
            UPDATE jobs SET status = 'pending', updated_at = ?
            WHERE status = 'retry_scheduled' AND run_after IS NULL
              AND leased_by IS NULL
            """,
            (updated_at.isoformat(),),
        )

    def task_rows(
        self,
        *,
        correlation_ids: tuple[str, ...],
        statuses: tuple[str, ...],
        limit: int,
        evidence_events: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        cid_ph = self._placeholders(correlation_ids)
        status_ph = self._placeholders(statuses)
        event_ph = self._placeholders(evidence_events)
        rows = self.repo.read_all(
            f"""
            SELECT j.id, j.type, j.status, j.payload_json, j.last_error,
                   j.leased_by, j.lease_expires_at, j.resource_key,
                   (SELECT e.message FROM job_events e
                    WHERE e.job_id = j.id AND e.event_type IN ({event_ph})
                    ORDER BY e.created_at DESC LIMIT 1) AS evidence
            FROM jobs j
            WHERE j.correlation_id IN ({cid_ph})
              AND j.status IN ({status_ph})
            ORDER BY CASE j.status
                WHEN 'running' THEN 0 WHEN 'pending' THEN 1
                WHEN 'retry_scheduled' THEN 2 ELSE 3 END,
                j.updated_at DESC
            LIMIT ?
            """,
            (*evidence_events, *correlation_ids, *statuses, limit),
        )
        return [dict(row) for row in rows]

    def completed_rows(
        self, *, correlation_ids: tuple[str, ...], limit: int
    ) -> list[dict[str, Any]]:
        cid_ph = self._placeholders(correlation_ids)
        rows = self.repo.read_all(
            f"""
            SELECT j.id, j.type, j.status, j.payload_json, j.last_error,
                   j.leased_by, j.lease_expires_at, j.resource_key,
                   (SELECT e.message FROM job_events e
                    WHERE e.job_id = j.id AND e.event_type = 'job_succeeded'
                    ORDER BY e.created_at DESC LIMIT 1) AS evidence
            FROM jobs j
            WHERE j.correlation_id IN ({cid_ph}) AND j.status = 'succeeded'
            ORDER BY j.updated_at DESC LIMIT ?
            """,
            (*correlation_ids, limit),
        )
        return [dict(row) for row in rows]

    def claim_exact(
        self,
        *,
        task_id: str,
        agent_id: str,
        agent_type: str,
        lease_seconds: int,
    ) -> tuple[str, dict[str, Any] | None]:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=max(10, min(lease_seconds, 3600)))
        with self.repo.transaction(immediate=True) as transaction:
            row = transaction.execute("SELECT * FROM jobs WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return "missing", None
            row_dict = dict(row)
            if row["status"] == "running":
                state = "owned" if row["leased_by"] == agent_id else "occupied"
                return state, row_dict
            if row["status"] != "pending":
                return str(row["status"]), row_dict
            if row["resource_key"] is not None:
                holder = transaction.execute(
                    """
                    SELECT leased_by FROM jobs
                    WHERE status = 'running' AND resource_key = ? AND id != ? LIMIT 1
                    """,
                    (row["resource_key"], task_id),
                ).fetchone()
                if holder is not None:
                    return "occupied", dict(holder)
            cursor = transaction.execute(
                """
                UPDATE jobs SET status = 'running', attempt = attempt + 1,
                    leased_by = ?, lease_expires_at = ?, heartbeat_at = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    agent_id,
                    expires.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                ),
            )
            if cursor.rowcount != 1:
                return "occupied", None
            self.repo.append_event_in_transaction(
                transaction,
                task_id,
                "job_claimed",
                metadata={
                    "worker_id": agent_id,
                    "agent_type": agent_type,
                    "lease_expires_at": expires.isoformat(),
                },
            )
            claimed = transaction.execute("SELECT * FROM jobs WHERE id = ?", (task_id,)).fetchone()
            return "claimed", dict(claimed) if claimed is not None else None

    def scoped_job(
        self, *, task_id: str, correlation_ids: tuple[str, ...]
    ) -> dict[str, Any] | None:
        cid_ph = self._placeholders(correlation_ids)
        row = self.repo.read_one(
            f"SELECT * FROM jobs WHERE id = ? AND correlation_id IN ({cid_ph})",
            (task_id, *correlation_ids),
        )
        return dict(row) if row is not None else None

    def owned_rows(
        self,
        *,
        correlation_ids: tuple[str, ...],
        job_types: tuple[str, ...],
        agent_id: str,
    ) -> list[dict[str, Any]]:
        cid_ph = self._placeholders(correlation_ids)
        type_ph = self._placeholders(job_types)
        rows = self.repo.read_all(
            f"""
            SELECT * FROM jobs
            WHERE correlation_id IN ({cid_ph})
              AND type IN ({type_ph})
              AND status = 'running' AND leased_by = ?
            ORDER BY updated_at DESC
            """,
            (*correlation_ids, *job_types, agent_id),
        )
        return [dict(row) for row in rows]


@dataclass(slots=True)
class PostgresWorkspaceRepository:
    repo: Any

    @staticmethod
    def _placeholders(values: tuple[Any, ...]) -> str:
        return ",".join("%s" for _ in values)

    def configure_runtime(self) -> None:
        return

    def recover_null_retries(self, *, updated_at: datetime) -> int:
        with self.repo._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs SET status = 'pending', updated_at = %s
                WHERE status = 'retry_scheduled' AND run_after IS NULL
                  AND leased_by IS NULL
                """,
                (updated_at,),
            )
            count = int(cur.rowcount)
        self.repo._conn.commit()
        return count

    def task_rows(
        self,
        *,
        correlation_ids: tuple[str, ...],
        statuses: tuple[str, ...],
        limit: int,
        evidence_events: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        cid_ph = self._placeholders(correlation_ids)
        status_ph = self._placeholders(statuses)
        event_ph = self._placeholders(evidence_events)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT j.id, j.type, j.status, j.payload_json, j.last_error,
                       j.leased_by, j.lease_expires_at, j.resource_key,
                       (SELECT e.message FROM job_events e
                        WHERE e.job_id = j.id AND e.event_type IN ({event_ph})
                        ORDER BY e.created_at DESC LIMIT 1) AS evidence
                FROM jobs j
                WHERE j.correlation_id IN ({cid_ph}) AND j.status IN ({status_ph})
                ORDER BY CASE j.status
                    WHEN 'running' THEN 0 WHEN 'pending' THEN 1
                    WHEN 'retry_scheduled' THEN 2 ELSE 3 END,
                    j.updated_at DESC LIMIT %s
                """,
                (*evidence_events, *correlation_ids, *statuses, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def completed_rows(
        self, *, correlation_ids: tuple[str, ...], limit: int
    ) -> list[dict[str, Any]]:
        cid_ph = self._placeholders(correlation_ids)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT j.id, j.type, j.status, j.payload_json, j.last_error,
                       j.leased_by, j.lease_expires_at, j.resource_key,
                       (SELECT e.message FROM job_events e
                        WHERE e.job_id = j.id AND e.event_type = 'job_succeeded'
                        ORDER BY e.created_at DESC LIMIT 1) AS evidence
                FROM jobs j
                WHERE j.correlation_id IN ({cid_ph}) AND j.status = 'succeeded'
                ORDER BY j.updated_at DESC LIMIT %s
                """,
                (*correlation_ids, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def claim_exact(
        self,
        *,
        task_id: str,
        agent_id: str,
        agent_type: str,
        lease_seconds: int,
    ) -> tuple[str, dict[str, Any] | None]:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=max(10, min(lease_seconds, 3600)))
        with self.repo._conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s FOR UPDATE", (task_id,))
            row = cur.fetchone()
            if row is None:
                self.repo._conn.rollback()
                return "missing", None
            if row["status"] == "running":
                self.repo._conn.commit()
                state = "owned" if row["leased_by"] == agent_id else "occupied"
                return state, dict(row)
            if row["status"] != "pending":
                self.repo._conn.commit()
                return str(row["status"]), dict(row)
            if row.get("resource_key") is not None:
                cur.execute(
                    """
                    SELECT leased_by FROM jobs
                    WHERE status = 'running' AND resource_key = %s AND id != %s LIMIT 1
                    """,
                    (row["resource_key"], task_id),
                )
                holder = cur.fetchone()
                if holder is not None:
                    self.repo._conn.commit()
                    return "occupied", dict(holder)
            cur.execute(
                """
                UPDATE jobs SET status = 'running', attempt = attempt + 1,
                    leased_by = %s, lease_expires_at = %s, heartbeat_at = %s,
                    started_at = COALESCE(started_at, %s), updated_at = %s
                WHERE id = %s AND status = 'pending'
                """,
                (agent_id, expires, now, now, now, task_id),
            )
            if cur.rowcount != 1:
                self.repo._conn.rollback()
                return "occupied", None
            self.repo._append_event(
                cur,
                task_id,
                "job_claimed",
                metadata={
                    "worker_id": agent_id,
                    "agent_type": agent_type,
                    "lease_expires_at": expires.isoformat(),
                },
            )
            cur.execute("SELECT * FROM jobs WHERE id = %s", (task_id,))
            claimed = cur.fetchone()
        self.repo._conn.commit()
        return "claimed", dict(claimed) if claimed is not None else None

    def scoped_job(
        self, *, task_id: str, correlation_ids: tuple[str, ...]
    ) -> dict[str, Any] | None:
        ph = self._placeholders(correlation_ids)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM jobs WHERE id = %s AND correlation_id IN ({ph})",
                (task_id, *correlation_ids),
            )
            row = cur.fetchone()
        return dict(row) if row is not None else None

    def owned_rows(
        self,
        *,
        correlation_ids: tuple[str, ...],
        job_types: tuple[str, ...],
        agent_id: str,
    ) -> list[dict[str, Any]]:
        cid_ph = self._placeholders(correlation_ids)
        type_ph = self._placeholders(job_types)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT * FROM jobs
                WHERE correlation_id IN ({cid_ph}) AND type IN ({type_ph})
                  AND status = 'running' AND leased_by = %s
                ORDER BY updated_at DESC
                """,
                (*correlation_ids, *job_types, agent_id),
            )
            return [dict(row) for row in cur.fetchall()]


def workspace_repository(repo: Any) -> HandoffRepository:
    if hasattr(repo, "_connection"):
        return SQLiteWorkspaceRepository(repo)
    if hasattr(repo, "_conn"):
        return PostgresWorkspaceRepository(repo)
    raise TypeError(f"unsupported repository adapter: {type(repo).__name__}")
