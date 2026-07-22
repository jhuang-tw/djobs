"""SQLite-backed job repository."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from djobs.core.errors import AgentNotFoundError, JobNotFoundError
from djobs.core.models import Agent, Job
from djobs.core.states import AgentStatus, JobStatus, validate_transition
from djobs.storage.events import JobEvent
from djobs.storage.schema import SQLITE_SCHEMA_SQL, apply_sqlite_column_migrations

DEFAULT_LEASE_DURATION = timedelta(seconds=30)
DEFAULT_AGENT_TIMEOUT = timedelta(seconds=90)

# Authoritative schema lives in djobs.storage.schema. Re-exported under the
# historical name for backward compatibility.
SCHEMA_SQL = SQLITE_SCHEMA_SQL


# JobEvent is re-exported from storage.events for backward compatibility.
# (imported at top of file)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for repository use."""
    if str(path) != ":memory:":
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create job tables and indexes if they do not already exist."""
    connection.executescript(SCHEMA_SQL)
    apply_sqlite_column_migrations(connection)
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
                    started_at, depends_on_json, resource_key, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            now = datetime.now(timezone.utc)
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

                # Precompute global aggregates ONCE instead of re-querying them
                # for every candidate row (avoids an N+1 query pattern):
                #  - running counts per type, for type_concurrency_limits
                #  - the set of resource_keys currently held by RUNNING jobs
                # A separate cursor is used for these and the per-candidate
                # dependency check so they don't disturb the lazy iteration of
                # the main candidate cursor below.
                inner = self._connection.cursor()
                running_by_type: dict[str, int] | None = None
                if type_concurrency_limits:
                    running_by_type = {
                        r["type"]: r["cnt"]
                        for r in inner.execute(
                            "SELECT type, COUNT(*) AS cnt FROM jobs "
                            "WHERE status = ? GROUP BY type",
                            (JobStatus.RUNNING.value,),
                        ).fetchall()
                    }
                held_resource_keys = {
                    r["resource_key"]
                    for r in inner.execute(
                        "SELECT DISTINCT resource_key FROM jobs "
                        "WHERE status = ? AND resource_key IS NOT NULL",
                        (JobStatus.RUNNING.value,),
                    ).fetchall()
                }

                row = None
                # Iterate the cursor lazily so we stop at the first claimable row
                # rather than loading every pending job into memory.
                for candidate in cursor.execute(sql, params):
                    if running_by_type is not None:
                        jtype = candidate["type"]
                        limit = type_concurrency_limits.get(jtype)  # type: ignore[union-attr]
                        if limit is not None and running_by_type.get(jtype, 0) >= limit:
                            continue
                    if candidate["resource_key"] in held_resource_keys:
                        continue
                    if not self._dependencies_satisfied(inner, candidate):
                        continue
                    row = candidate
                    break

                if row is None:
                    self._connection.commit()
                    return None

                job = _row_to_job(row)
                validate_transition(job.status, JobStatus.RUNNING)
                updated_at = _serialize_datetime(datetime.now(timezone.utc))
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

    def mark_succeeded(self, job_id: str, *, evidence: str | None = None) -> Job:
        with self._lock:
            job = self.require_job(job_id)
            validate_transition(job.status, JobStatus.SUCCEEDED)
            self._update_status(job_id, JobStatus.SUCCEEDED, last_error=None)
            self._clear_lease(job_id)
            metadata = {"evidence": evidence} if evidence else {}
            self._append_event(
                job_id,
                "job_succeeded",
                message=evidence,
                metadata=metadata,
            )
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
                    _serialize_datetime(datetime.now(timezone.utc)),
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

    def mark_archived(self, job_id: str, reason: str | None = None) -> Job:
        """Archive a job so it no longer appears as active AI workflow state."""
        with self._lock:
            job = self.require_job(job_id)
            validate_transition(job.status, JobStatus.ARCHIVED)
            self._update_status(job_id, JobStatus.ARCHIVED, last_error=None)
            self._clear_lease(job_id)
            self._append_event(
                job_id,
                "job_archived",
                message=reason,
                metadata={"previous_status": job.status.value},
            )
            self._connection.commit()
            return self.require_job(job_id)

    def release_job(self, job_id: str, worker_id: str, reason: str | None = None) -> Job:
        """Release a claimed (RUNNING) job back to PENDING.

        Used when an agent voluntarily gives up a task it has claimed (e.g.
        it cannot make progress, or is shutting down gracefully).  The job
        becomes available for another agent to claim immediately.
        """
        with self._lock:
            job = self.require_job(job_id)
            if job.status != JobStatus.RUNNING:
                raise JobNotFoundError(
                    f"Cannot release job {job_id!r} in status {job.status.value!r}"
                )
            if job.leased_by != worker_id:
                raise JobNotFoundError(f"Job {job_id!r} is not leased by worker {worker_id!r}")
            validate_transition(job.status, JobStatus.PENDING)
            self._update_status(job_id, JobStatus.PENDING, last_error=None)
            self._clear_lease(job_id)
            self._append_event(
                job_id,
                "job_released",
                message=reason,
                metadata={"worker_id": worker_id},
            )
            self._connection.commit()
            return self.require_job(job_id)

    def promote_due_retries(self, now: datetime | None = None) -> list[Job]:
        with self._lock:
            current_time = _serialize_datetime(now or datetime.now(timezone.utc))
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
                        _serialize_datetime(datetime.now(timezone.utc)),
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
            now = datetime.now(timezone.utc)
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
            current_time = _serialize_datetime(now or datetime.now(timezone.utc))
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
                        _serialize_datetime(datetime.now(timezone.utc)),
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

    def list_by_status(self, status: str, limit: int = 100) -> list[Job]:
        """Return jobs matching the given status string."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            return [_row_to_job(row) for row in rows]

    def list_jobs_by_correlation_ids(
        self,
        correlation_ids: list[str],
        statuses: tuple[str, ...] | None = None,
    ) -> list[Job]:
        """Return jobs for any of *correlation_ids*, optionally status-filtered.

        Hydrates full rows in a single locked query (no per-row ``get_job``
        round trip), so ``resume_session`` / ``list_tasks`` stay one query
        regardless of task count. Results are ordered by ``rowid`` (strict
        insertion order), which is stable even when ``created_at`` collides
        within a clock tick.
        """
        if not correlation_ids:
            return []
        cid_ph = ",".join("?" for _ in correlation_ids)
        with self._lock:
            if statuses:
                status_ph = ",".join("?" for _ in statuses)
                rows = self._connection.execute(
                    f"SELECT * FROM jobs WHERE correlation_id IN ({cid_ph}) "
                    f"AND status IN ({status_ph}) ORDER BY rowid ASC",
                    (*correlation_ids, *statuses),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    f"SELECT * FROM jobs WHERE correlation_id IN ({cid_ph}) ORDER BY rowid ASC",
                    (*correlation_ids,),
                ).fetchall()
            return [_row_to_job(row) for row in rows]

    def count_stuck_running(self, now: datetime | None = None) -> int:
        """Count running jobs whose lease has expired (stuck tasks)."""
        if now is None:
            now = datetime.now(timezone.utc)
        with self._lock:
            row = self._connection.execute(
                """SELECT COUNT(*) AS cnt FROM jobs
                   WHERE status = ? AND lease_expires_at IS NOT NULL
                   AND lease_expires_at < ?""",
                (JobStatus.RUNNING.value, _serialize_datetime(now)),
            ).fetchone()
            return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Agent registry (Phase M4)
    # ------------------------------------------------------------------

    def register_agent(
        self,
        agent_id: str,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Agent:
        """Register or re-register an agent (upsert); marks it ONLINE."""
        with self._lock:
            now = datetime.now(timezone.utc)
            existing = self._connection.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if existing is None:
                agent = Agent(
                    id=agent_id,
                    status=AgentStatus.ONLINE,
                    capabilities=list(capabilities or []),
                    metadata=dict(metadata or {}),
                    registered_at=now,
                    last_heartbeat_at=now,
                )
                self._connection.execute(
                    """
                    INSERT INTO agents (
                        id, status, capabilities_json, metadata_json,
                        registered_at, last_heartbeat_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    _agent_to_params(agent),
                )
            else:
                agent = _row_to_agent(existing)
                agent.status = AgentStatus.ONLINE
                if capabilities is not None:
                    agent.capabilities = list(capabilities)
                if metadata is not None:
                    agent.metadata = dict(metadata)
                agent.last_heartbeat_at = now
                self._connection.execute(
                    """
                    UPDATE agents
                    SET status = ?, capabilities_json = ?, metadata_json = ?,
                        last_heartbeat_at = ?
                    WHERE id = ?
                    """,
                    (
                        agent.status.value,
                        json.dumps(agent.capabilities, ensure_ascii=False),
                        json.dumps(agent.metadata, ensure_ascii=False, sort_keys=True),
                        _serialize_datetime(agent.last_heartbeat_at),
                        agent_id,
                    ),
                )
            self._connection.commit()
            return agent

    def agent_heartbeat(self, agent_id: str) -> Agent:
        """Record a liveness ping; brings the agent back ONLINE if reaped."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                raise AgentNotFoundError(f"Agent {agent_id!r} is not registered")
            now = datetime.now(timezone.utc)
            self._connection.execute(
                "UPDATE agents SET status = ?, last_heartbeat_at = ? WHERE id = ?",
                (AgentStatus.ONLINE.value, _serialize_datetime(now), agent_id),
            )
            self._connection.commit()
            agent = _row_to_agent(row)
            agent.status = AgentStatus.ONLINE
            agent.last_heartbeat_at = now
            return agent

    def get_agent(self, agent_id: str) -> Agent | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            return _row_to_agent(row) if row else None

    def list_agents(self, status: str | None = None) -> list[Agent]:
        with self._lock:
            if status is None:
                rows = self._connection.execute(
                    "SELECT * FROM agents ORDER BY registered_at ASC"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM agents WHERE status = ? ORDER BY registered_at ASC",
                    (status,),
                ).fetchall()
            return [_row_to_agent(row) for row in rows]

    def mark_stale_agents_offline(
        self,
        timeout: timedelta = DEFAULT_AGENT_TIMEOUT,
        now: datetime | None = None,
    ) -> list[Agent]:
        """Mark ONLINE agents whose last heartbeat is older than *timeout* OFFLINE."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timeout
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM agents WHERE status = ? AND last_heartbeat_at < ?",
                (AgentStatus.ONLINE.value, _serialize_datetime(cutoff)),
            ).fetchall()
            marked: list[Agent] = []
            for row in rows:
                self._connection.execute(
                    "UPDATE agents SET status = ? WHERE id = ?",
                    (AgentStatus.OFFLINE.value, row["id"]),
                )
                agent = _row_to_agent(row)
                agent.status = AgentStatus.OFFLINE
                marked.append(agent)
            if marked:
                self._connection.commit()
            return marked

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
                _serialize_datetime(datetime.now(timezone.utc)),
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

    def _dependencies_satisfied(self, cursor: sqlite3.Cursor, row: sqlite3.Row) -> bool:
        """Return True if every dependency of *row* has succeeded.

        A job with unmet dependencies is not claimable yet.
        """
        raw = row["depends_on_json"]
        if not raw:
            return True
        dep_ids = json.loads(raw)
        if not dep_ids:
            return True
        unique_ids = set(dep_ids)
        placeholders = ", ".join("?" * len(unique_ids))
        satisfied = cursor.execute(
            f"""
            SELECT COUNT(*) AS cnt FROM jobs
            WHERE id IN ({placeholders}) AND status = ?
            """,
            (*unique_ids, JobStatus.SUCCEEDED.value),
        ).fetchone()["cnt"]
        return satisfied == len(unique_ids)


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
        json.dumps(job.depends_on, ensure_ascii=False),
        job.resource_key,
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
        depends_on=json.loads(row["depends_on_json"]) if row["depends_on_json"] else [],
        resource_key=row["resource_key"],
        created_at=_parse_datetime_required(row["created_at"]),
        updated_at=_parse_datetime_required(row["updated_at"]),
    )


def _agent_to_params(agent: Agent) -> tuple[Any, ...]:
    return (
        agent.id,
        agent.status.value,
        json.dumps(agent.capabilities, ensure_ascii=False),
        json.dumps(agent.metadata, ensure_ascii=False, sort_keys=True),
        _serialize_datetime(agent.registered_at),
        _serialize_datetime(agent.last_heartbeat_at),
    )


def _row_to_agent(row: sqlite3.Row) -> Agent:
    return Agent(
        id=row["id"],
        status=AgentStatus(row["status"]),
        capabilities=json.loads(row["capabilities_json"]) if row["capabilities_json"] else [],
        metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        registered_at=_parse_datetime_required(row["registered_at"]),
        last_heartbeat_at=_parse_datetime_required(row["last_heartbeat_at"]),
    )


def _row_to_event(row: sqlite3.Row) -> JobEvent:
    metadata_json = row["metadata_json"]
    return JobEvent(
        id=row["id"],
        job_id=row["job_id"],
        event_type=row["event_type"],
        message=row["message"],
        metadata=json.loads(metadata_json) if metadata_json else {},
        created_at=_parse_datetime_required(row["created_at"]),
    )


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_datetime_required(value: str | None) -> datetime:
    """Parse a NOT NULL timestamp column; raises if the value is unexpectedly NULL."""
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ValueError("expected a non-null timestamp")
    return parsed
