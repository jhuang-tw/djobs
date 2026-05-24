"""SQLite-backed job repository."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from djobs.core.errors import JobNotFoundError
from djobs.core.models import Job
from djobs.core.states import JobStatus, validate_transition
from djobs.storage.events import JobEvent

DEFAULT_LEASE_DURATION = timedelta(seconds=30)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    run_after TEXT NULL,
    idempotency_key TEXT NULL,
    correlation_id TEXT NOT NULL,
    last_error TEXT NULL,
    leased_by TEXT NULL,
    lease_expires_at TEXT NULL,
    heartbeat_at TEXT NULL,
    started_at TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_claimable
ON jobs (status, run_after, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_idempotency_key
ON jobs (idempotency_key)
WHERE idempotency_key IS NOT NULL
    AND status IN ('pending', 'running', 'retry_scheduled');

-- Phase 9.5: index for audit_log / list_tasks / resume_session correlation_id lookups.
CREATE INDEX IF NOT EXISTS idx_jobs_correlation_id
ON jobs (correlation_id);

CREATE TABLE IF NOT EXISTS job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NULL,
    metadata_json TEXT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_created
ON job_events (job_id, created_at);

-- Phase 9.5: index for audit_log time-range queries spanning all jobs.
CREATE INDEX IF NOT EXISTS idx_job_events_created_at
ON job_events (created_at);
"""


# JobEvent is re-exported from storage.events for backward compatibility.
# (imported at top of file)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for repository use."""
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create job tables and indexes if they do not already exist."""
    connection.executescript(SCHEMA_SQL)
    connection.commit()


class SQLiteJobRepository:
    """Repository that persists jobs and lifecycle events in SQLite.

    All public methods are protected by a reentrant lock for thread safety.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()

    @classmethod
    def from_path(cls, path: str | Path) -> SQLiteJobRepository:
        connection = connect(path)
        initialize_schema(connection)
        return cls(connection)

    def create_job(self, job: Job) -> Job:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO jobs (
                    id, type, payload_json, status, attempt, max_attempts,
                    run_after, idempotency_key, correlation_id, last_error,
                    leased_by, lease_expires_at, heartbeat_at,
                    started_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _job_to_params(job),
            )
            self._append_event(
                job.id,
                "job_created",
                metadata={"job_type": job.type, "correlation_id": job.correlation_id},
            )
            self._connection.commit()
            return job

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return _row_to_job(row)

    def find_active_by_idempotency_key(self, idempotency_key: str) -> Job | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM jobs
                WHERE idempotency_key = ?
                  AND status IN (?, ?, ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (
                    idempotency_key,
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.RETRY_SCHEDULED.value,
                ),
            ).fetchone()
            if row is None:
                return None
            return _row_to_job(row)

    def require_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} was not found")
        return job

    def claim_next_job(
        self,
        worker_id: str,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
        type_concurrency_limits: dict[str, int] | None = None,
        type_filter: list[str] | None = None,
    ) -> Job | None:
        with self._lock:
            now = datetime.now(UTC)
            now_str = _serialize_datetime(now)
            lease_expires = _serialize_datetime(now + lease_duration)

            # Empty type_filter means "nothing to claim".
            if type_filter is not None and not type_filter:
                return None

            cursor = self._connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")

                if type_filter is not None:
                    placeholders = ", ".join("?" * len(type_filter))
                    sql = f"""
                        SELECT * FROM jobs
                        WHERE status = ?
                          AND (run_after IS NULL OR run_after <= ?)
                          AND type IN ({placeholders})
                        ORDER BY created_at ASC
                    """
                    params: tuple[Any, ...] = (
                        JobStatus.PENDING.value,
                        now_str,
                        *type_filter,
                    )
                else:
                    sql = """
                        SELECT * FROM jobs
                        WHERE status = ?
                          AND (run_after IS NULL OR run_after <= ?)
                        ORDER BY created_at ASC
                    """
                    params = (JobStatus.PENDING.value, now_str)

                rows = cursor.execute(sql, params).fetchall()

                row = None
                for candidate in rows:
                    if type_concurrency_limits is not None:
                        jtype = candidate["type"]
                        limit = type_concurrency_limits.get(jtype)
                        if limit is not None:
                            running = cursor.execute(
                                "SELECT COUNT(*) AS cnt FROM jobs WHERE status = ? AND type = ?",
                                (JobStatus.RUNNING.value, jtype),
                            ).fetchone()["cnt"]
                            if running >= limit:
                                continue
                    row = candidate
                    break

                if row is None:
                    self._connection.commit()
                    return None

                job = _row_to_job(row)
                validate_transition(job.status, JobStatus.RUNNING)
                updated_at = _serialize_datetime(datetime.now(UTC))
                cursor.execute(
                    """
                    UPDATE jobs
                    SET status = ?, attempt = attempt + 1, run_after = NULL,
                        leased_by = ?, lease_expires_at = ?, heartbeat_at = ?,
                        started_at = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        JobStatus.RUNNING.value,
                        worker_id,
                        lease_expires,
                        now_str,
                        now_str,
                        updated_at,
                        job.id,
                        JobStatus.PENDING.value,
                    ),
                )
                self._append_event(
                    job.id,
                    "job_claimed",
                    metadata={
                        "worker_id": worker_id,
                        "attempt": job.attempt + 1,
                        "lease_expires_at": lease_expires,
                    },
                )
                self._connection.commit()
                return self.require_job(job.id)
            except Exception:
                self._connection.rollback()
                raise

    def mark_succeeded(self, job_id: str) -> Job:
        with self._lock:
            job = self.require_job(job_id)
            validate_transition(job.status, JobStatus.SUCCEEDED)
            self._update_status(job_id, JobStatus.SUCCEEDED, last_error=None)
            self._clear_lease(job_id)
            self._append_event(job_id, "job_succeeded")
            self._connection.commit()
            return self.require_job(job_id)

    def mark_failed(self, job_id: str, error: str) -> Job:
        with self._lock:
            job = self.require_job(job_id)
            validate_transition(job.status, JobStatus.FAILED)
            self._update_status(job_id, JobStatus.FAILED, last_error=error)
            self._clear_lease(job_id)
            self._append_event(job_id, "job_failed", message=error)
            self._connection.commit()
            return self.require_job(job_id)

    def mark_retry_scheduled(self, job_id: str, error: str, run_after: datetime) -> Job:
        with self._lock:
            job = self.require_job(job_id)
            validate_transition(job.status, JobStatus.RETRY_SCHEDULED)
            self._connection.execute(
                """
                UPDATE jobs
                SET status = ?, last_error = ?, run_after = ?,
                    leased_by = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    JobStatus.RETRY_SCHEDULED.value,
                    error,
                    _serialize_datetime(run_after),
                    _serialize_datetime(datetime.now(UTC)),
                    job_id,
                ),
            )
            self._append_event(
                job_id,
                "retry_scheduled",
                message=error,
                metadata={
                    "attempt": job.attempt,
                    "run_after": _serialize_datetime(run_after),
                },
            )
            self._connection.commit()
            return self.require_job(job_id)

    def mark_dead_lettered(self, job_id: str, error: str) -> Job:
        with self._lock:
            job = self.require_job(job_id)
            validate_transition(job.status, JobStatus.DEAD_LETTERED)
            self._update_status(job_id, JobStatus.DEAD_LETTERED, last_error=error)
            self._clear_lease(job_id)
            self._append_event(
                job_id,
                "job_dead_lettered",
                message=error,
                metadata={
                    "attempt": job.attempt,
                    "max_attempts": job.max_attempts,
                },
            )
            self._connection.commit()
            return self.require_job(job_id)

    def promote_due_retries(self, now: datetime | None = None) -> list[Job]:
        with self._lock:
            current_time = _serialize_datetime(now or datetime.now(UTC))
            rows = self._connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                  AND run_after IS NOT NULL
                  AND run_after <= ?
                ORDER BY run_after ASC, created_at ASC
                """,
                (JobStatus.RETRY_SCHEDULED.value, current_time),
            ).fetchall()
            promoted_jobs: list[Job] = []
            for row in rows:
                job = _row_to_job(row)
                validate_transition(job.status, JobStatus.PENDING)
                self._connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, run_after = NULL, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        JobStatus.PENDING.value,
                        _serialize_datetime(datetime.now(UTC)),
                        job.id,
                        JobStatus.RETRY_SCHEDULED.value,
                    ),
                )
                self._append_event(job.id, "retry_promoted")
                promoted = self.require_job(job.id)
                promoted_jobs.append(promoted)
            self._connection.commit()
            return promoted_jobs

    def append_event(
        self,
        job_id: str,
        event_type: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        commit: bool = True,
    ) -> JobEvent:
        with self._lock:
            event = self._append_event(job_id, event_type, message, metadata)
            if commit:
                self._connection.commit()
            return event

    def list_events(self, job_id: str | None = None) -> list[JobEvent]:
        with self._lock:
            if job_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM job_events ORDER BY created_at ASC"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM job_events WHERE job_id = ? ORDER BY created_at ASC",
                    (job_id,),
                ).fetchall()
            return [_row_to_event(row) for row in rows]

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    ) -> Job:
        with self._lock:
            job = self.require_job(job_id)
            if job.status != JobStatus.RUNNING:
                raise JobNotFoundError(
                    f"Cannot heartbeat job {job_id!r} in status {job.status.value!r}"
                )
            if job.leased_by != worker_id:
                raise JobNotFoundError(f"Job {job_id!r} is not leased by worker {worker_id!r}")
            now = datetime.now(UTC)
            new_expires = now + lease_duration
            self._connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, heartbeat_at = ?,
                    updated_at = ?
                WHERE id = ? AND leased_by = ?
                """,
                (
                    _serialize_datetime(new_expires),
                    _serialize_datetime(now),
                    _serialize_datetime(now),
                    job_id,
                    worker_id,
                ),
            )
            self._connection.commit()
            return self.require_job(job_id)

    def recover_expired_leases(self, now: datetime | None = None) -> list[Job]:
        with self._lock:
            current_time = _serialize_datetime(now or datetime.now(UTC))
            rows = self._connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at ASC
                """,
                (JobStatus.RUNNING.value, current_time),
            ).fetchall()
            recovered: list[Job] = []
            for row in rows:
                job = _row_to_job(row)
                if job.attempt < job.max_attempts:
                    target_status = JobStatus.PENDING
                else:
                    target_status = JobStatus.RETRY_SCHEDULED
                self._connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, leased_by = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        target_status.value,
                        _serialize_datetime(datetime.now(UTC)),
                        job.id,
                        JobStatus.RUNNING.value,
                    ),
                )
                self._append_event(
                    job.id,
                    "lease_expired",
                    message=f"Worker {job.leased_by!r} lease expired",
                    metadata={
                        "worker_id": job.leased_by,
                        "attempt": job.attempt,
                        "recovered_to": target_status.value,
                    },
                )
                recovered.append(self.require_job(job.id))
            self._connection.commit()
            return recovered

    def count_by_status(self) -> dict[str, int]:
        """Return job counts grouped by status."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status"
            ).fetchall()
            return {row["status"]: row["cnt"] for row in rows}

    def count_running_by_type(self, job_type: str) -> int:
        """Return the number of RUNNING jobs for a given type."""
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS cnt FROM jobs WHERE status = ? AND type = ?",
                (JobStatus.RUNNING.value, job_type),
            ).fetchone()
            return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal helpers (called within lock)
    # ------------------------------------------------------------------

    def _append_event(
        self,
        job_id: str,
        event_type: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobEvent:
        event = JobEvent(
            job_id=job_id,
            event_type=event_type,
            message=message,
            metadata=metadata or {},
        )
        self._connection.execute(
            """
            INSERT INTO job_events
                (id, job_id, event_type, message, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.job_id,
                event.event_type,
                event.message,
                json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
                _serialize_datetime(event.created_at),
            ),
        )
        return event

    def _update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        last_error: str | None,
    ) -> None:
        self._connection.execute(
            """
            UPDATE jobs
            SET status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status.value,
                last_error,
                _serialize_datetime(datetime.now(UTC)),
                job_id,
            ),
        )

    def _clear_lease(self, job_id: str) -> None:
        self._connection.execute(
            """
            UPDATE jobs
            SET leased_by = NULL, lease_expires_at = NULL,
                heartbeat_at = NULL
            WHERE id = ?
            """,
            (job_id,),
        )


def _job_to_params(job: Job) -> tuple[Any, ...]:
    return (
        job.id,
        job.type,
        json.dumps(job.payload, ensure_ascii=False, sort_keys=True),
        job.status.value,
        job.attempt,
        job.max_attempts,
        _serialize_datetime(job.run_after),
        job.idempotency_key,
        job.correlation_id,
        job.last_error,
        job.leased_by,
        _serialize_datetime(job.lease_expires_at),
        _serialize_datetime(job.heartbeat_at),
        _serialize_datetime(job.started_at),
        _serialize_datetime(job.created_at),
        _serialize_datetime(job.updated_at),
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        type=row["type"],
        payload=json.loads(row["payload_json"]),
        status=JobStatus(row["status"]),
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        run_after=_parse_datetime(row["run_after"]),
        idempotency_key=row["idempotency_key"],
        correlation_id=row["correlation_id"],
        last_error=row["last_error"],
        leased_by=row["leased_by"],
        lease_expires_at=_parse_datetime(row["lease_expires_at"]),
        heartbeat_at=_parse_datetime(row["heartbeat_at"]),
        started_at=_parse_datetime(row["started_at"]),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _row_to_event(row: sqlite3.Row) -> JobEvent:
    metadata_json = row["metadata_json"]
    return JobEvent(
        id=row["id"],
        job_id=row["job_id"],
        event_type=row["event_type"],
        message=row["message"],
        metadata=json.loads(metadata_json) if metadata_json else {},
        created_at=_parse_datetime(row["created_at"]),
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
