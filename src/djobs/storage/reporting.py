"""Named read models for CLI, MCP, analytics, and delta recovery.

This adapter keeps reporting SQL inside the storage package.  Higher layers ask
for domain-shaped rows instead of issuing backend-specific queries.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SQLiteReportingRepository:
    repo: Any

    @staticmethod
    def _placeholders(values: Iterable[Any]) -> str:
        return ",".join("?" for _ in values)

    def latest_success_events(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.repo.read_all(
                """
                SELECT job_id, message, metadata_json, created_at
                FROM job_events
                WHERE event_type = 'job_succeeded'
                ORDER BY created_at ASC, rowid ASC
                """
            )
        ]

    def latest_success_evidence(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.repo.read_all(
                """
                SELECT job_id, message
                FROM job_events
                WHERE event_type = 'job_succeeded'
                ORDER BY created_at DESC, rowid DESC
                """
            )
        ]

    def status_rows(self, correlation_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        columns = (
            "id, type, status, payload_json, correlation_id, created_at, updated_at, "
            "attempt, max_attempts, last_error, depends_on_json"
        )
        if correlation_ids:
            placeholders = self._placeholders(correlation_ids)
            sql = (
                f"SELECT {columns} FROM jobs WHERE correlation_id IN ({placeholders}) "
                "AND status != ? ORDER BY rowid ASC"
            )
            params: tuple[Any, ...] = (*correlation_ids, "archived")
        else:
            sql = f"SELECT {columns} FROM jobs WHERE status != ? ORDER BY rowid ASC"
            params = ("archived",)
        return [dict(row) for row in self.repo.read_all(sql, params)]

    def task_position(self, job_id: str) -> dict[str, Any] | None:
        row = self.repo.read_one(
            "SELECT rowid, correlation_id FROM jobs WHERE id = ?",
            (job_id,),
        )
        return dict(row) if row is not None else None

    def jobs_before(self, correlation_id: str, rowid: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.repo.read_all(
                """
                SELECT id, status FROM jobs
                WHERE correlation_id = ? AND rowid < ?
                ORDER BY rowid ASC
                """,
                (correlation_id, rowid),
            )
        ]

    def workflow_states(self, correlation_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        if correlation_ids:
            placeholders = self._placeholders(correlation_ids)
            sql = (
                f"SELECT id, status FROM jobs WHERE correlation_id IN ({placeholders}) "
                "ORDER BY rowid ASC"
            )
            params: tuple[Any, ...] = correlation_ids
        else:
            sql = "SELECT id, status FROM jobs ORDER BY rowid ASC"
            params = ()
        return [dict(row) for row in self.repo.read_all(sql, params)]

    def delete_task(self, job_id: str) -> int:
        with self.repo.transaction(immediate=True) as transaction:
            row = transaction.execute(
                "SELECT COUNT(*) AS count FROM job_events WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            event_count = int(row["count"] if row is not None else 0)
            transaction.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
            transaction.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return event_count

    def receipt_rows(self, correlation_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        columns = "id, type, status, payload_json, correlation_id, last_error"
        if correlation_ids:
            placeholders = self._placeholders(correlation_ids)
            sql = (
                f"SELECT {columns} FROM jobs WHERE correlation_id IN ({placeholders}) "
                "ORDER BY rowid ASC"
            )
            params: tuple[Any, ...] = correlation_ids
        else:
            sql = f"SELECT {columns} FROM jobs ORDER BY correlation_id, rowid ASC"
            params = ()
        return [dict(row) for row in self.repo.read_all(sql, params)]

    def visible_rows(self, correlation_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        columns = (
            "id, type, status, payload_json, correlation_id, created_at, updated_at, "
            "attempt, max_attempts, last_error, run_after, leased_by, lease_expires_at, "
            "depends_on_json, resource_key"
        )
        if correlation_ids:
            placeholders = self._placeholders(correlation_ids)
            sql = (
                f"SELECT {columns} FROM jobs WHERE status NOT IN ('succeeded', 'archived') "
                f"AND correlation_id IN ({placeholders}) ORDER BY rowid ASC"
            )
            params: tuple[Any, ...] = correlation_ids
        else:
            sql = (
                f"SELECT {columns} FROM jobs WHERE status NOT IN ('succeeded', 'archived') "
                "ORDER BY correlation_id, rowid ASC"
            )
            params = ()
        return [dict(row) for row in self.repo.read_all(sql, params)]

    def dependency_statuses(self, job_ids: tuple[str, ...]) -> dict[str, str]:
        if not job_ids:
            return {}
        placeholders = self._placeholders(job_ids)
        return {
            str(row["id"]): str(row["status"])
            for row in self.repo.read_all(
                f"SELECT id, status FROM jobs WHERE id IN ({placeholders})",
                job_ids,
            )
        }

    def resource_holders(self) -> dict[str, str]:
        holders: dict[str, str] = {}
        rows = self.repo.read_all(
            "SELECT id, resource_key FROM jobs "
            "WHERE status = 'running' AND resource_key IS NOT NULL ORDER BY created_at, rowid"
        )
        for row in rows:
            holders.setdefault(str(row["resource_key"]), str(row["id"]))
        return holders

    def token_saving_rows(self, correlation_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        columns = "id, type, status, payload_json, correlation_id, last_error"
        if correlation_ids:
            placeholders = self._placeholders(correlation_ids)
            sql = (
                f"SELECT {columns} FROM jobs WHERE correlation_id IN ({placeholders}) "
                "AND status != ? ORDER BY rowid ASC"
            )
            params: tuple[Any, ...] = (*correlation_ids, "archived")
        else:
            sql = (
                f"SELECT {columns} FROM jobs WHERE status != ? ORDER BY correlation_id, rowid ASC"
            )
            params = ("archived",)
        return [dict(row) for row in self.repo.read_all(sql, params)]

    def gain_job_rows(self, correlation_ids: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        columns = (
            "id, type, status, payload_json, correlation_id, last_error, attempt, "
            "max_attempts, started_at, created_at, updated_at"
        )
        if correlation_ids:
            placeholders = self._placeholders(correlation_ids)
            sql = (
                f"SELECT {columns} FROM jobs WHERE correlation_id IN ({placeholders}) "
                "ORDER BY created_at ASC, rowid ASC"
            )
            params: tuple[Any, ...] = correlation_ids
        else:
            sql = f"SELECT {columns} FROM jobs ORDER BY created_at ASC, rowid ASC"
            params = ()
        return [dict(row) for row in self.repo.read_all(sql, params)]

    def workspace_events(
        self,
        correlation_ids: tuple[str, ...],
        since_revision: int,
        *,
        limit: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        if not correlation_ids:
            return 0, []
        placeholders = self._placeholders(correlation_ids)
        head = self.repo.read_one(
            f"SELECT COALESCE(MAX(revision), 0) AS revision FROM context_revisions "
            f"WHERE correlation_id IN ({placeholders})",
            correlation_ids,
        )
        rows = self.repo.read_all(
            f"""
            SELECT revision, job_id, task_type, event_type, status, payload_json,
                   attempt, max_attempts, last_error, run_after, depends_on_json,
                   resource_key
            FROM context_revisions
            WHERE correlation_id IN ({placeholders}) AND revision > ?
            ORDER BY revision ASC
            LIMIT ?
            """,
            (*correlation_ids, since_revision, limit),
        )
        return int(head["revision"] if head else 0), [dict(row) for row in rows]

    def audit_rows(
        self,
        *,
        since: str,
        until: str | None = None,
        correlation_id: str | None = None,
        event_type: str | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        where = ["e.created_at >= ?"]
        params: list[Any] = [since]
        if until is not None:
            where.append("e.created_at <= ?")
            params.append(until)
        if correlation_id:
            where.append("j.correlation_id = ?")
            params.append(correlation_id)
        if event_type:
            where.append("e.event_type = ?")
            params.append(event_type)
        rows = self.repo.read_all(
            f"""
            SELECT e.id AS event_id, e.job_id AS job_id,
                   e.event_type AS event_type, e.message AS message,
                   e.created_at AS event_at, j.type AS task_type,
                   j.status AS job_status, j.correlation_id AS correlation_id,
                   j.created_at AS job_created_at, j.updated_at AS job_updated_at
            FROM job_events e
            JOIN jobs j ON e.job_id = j.id
            WHERE {" AND ".join(where)}
            ORDER BY e.created_at DESC, e.rowid DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        )
        return [dict(row) for row in rows]


def reporting_repository(repo: Any) -> SQLiteReportingRepository:
    if not hasattr(repo, "read_all") or not hasattr(repo, "read_one"):
        raise TypeError(f"reporting is unsupported for {type(repo).__name__}")
    return SQLiteReportingRepository(repo)
