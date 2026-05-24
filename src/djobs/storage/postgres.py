"""PostgreSQL-backed job repository.

Uses ``psycopg`` (v3) with ``SELECT ... FOR UPDATE SKIP LOCKED`` for
atomic, contention-free job claiming in multi-worker deployments.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from djobs.core.errors import JobNotFoundError
from djobs.core.models import Job
from djobs.core.states import JobStatus, validate_transition
from djobs.storage.events import JobEvent

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError as _exc:
    raise ImportError(
        "psycopg is required for PostgreSQL support. "
        "Install it with: pip install 'djobs[pg]'"
    ) from _exc

DEFAULT_LEASE_DURATION = timedelta(seconds=30)

PG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    run_after TIMESTAMPTZ NULL,
    idempotency_key TEXT NULL,
    correlation_id TEXT NOT NULL,
    last_error TEXT NULL,
    leased_by TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    heartbeat_at TIMESTAMPTZ NULL,
    started_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_claimable
ON jobs (status, run_after, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_idempotency_key
ON jobs (idempotency_key)
WHERE idempotency_key IS NOT NULL
    AND status IN ('pending', 'running', 'retry_scheduled');

CREATE TABLE IF NOT EXISTS job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NULL,
    metadata_json TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_created
ON job_events (job_id, created_at);
"""


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    # string fallback
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _row_to_job(row: dict[str, Any]) -> Job:
    return Job(
        id=row["id"],
        type=row["type"],
        payload=json.loads(row["payload_json"]),
        status=JobStatus(row["status"]),
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        run_after=_parse_dt(row["run_after"]),
        idempotency_key=row["idempotency_key"],
        correlation_id=row["correlation_id"],
        last_error=row["last_error"],
        leased_by=row["leased_by"],
        lease_expires_at=_parse_dt(row["lease_expires_at"]),
        heartbeat_at=_parse_dt(row["heartbeat_at"]),
        started_at=_parse_dt(row["started_at"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_event(row: dict[str, Any]) -> JobEvent:
    meta = row["metadata_json"]
    return JobEvent(
        id=row["id"],
        job_id=row["job_id"],
        event_type=row["event_type"],
        message=row["message"],
        metadata=json.loads(meta) if meta else {},
        created_at=_parse_dt(row["created_at"]),
    )


class PostgresJobRepository:
    """Repository that persists jobs and lifecycle events in PostgreSQL.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` for contention-free claiming.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresJobRepository:
        conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        cls._initialize_schema(conn)
        return cls(conn)

    @staticmethod
    def _initialize_schema(conn: psycopg.Connection) -> None:
        with conn.cursor() as cur:
            cur.execute(PG_SCHEMA_SQL)
        conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _append_event(
        self,
        cur: psycopg.Cursor,
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
        cur.execute(
            """
            INSERT INTO job_events (id, job_id, event_type, message, metadata_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                event.id,
                event.job_id,
                event.event_type,
                event.message,
                json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
                _serialize_dt(event.created_at),
            ),
        )
        return event

    def _get_job(self, cur: psycopg.Cursor, job_id: str) -> Job | None:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def _require_job(self, cur: psycopg.Cursor, job_id: str) -> Job:
        job = self._get_job(cur, job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} was not found")
        return job

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_job(self, job: Job) -> Job:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    id, type, payload_json, status, attempt, max_attempts,
                    run_after, idempotency_key, correlation_id, last_error,
                    leased_by, lease_expires_at, heartbeat_at,
                    started_at, created_at, updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    job.id,
                    job.type,
                    json.dumps(job.payload, ensure_ascii=False, sort_keys=True),
                    job.status.value,
                    job.attempt,
                    job.max_attempts,
                    _serialize_dt(job.run_after),
                    job.idempotency_key,
                    job.correlation_id,
                    job.last_error,
                    job.leased_by,
                    _serialize_dt(job.lease_expires_at),
                    _serialize_dt(job.heartbeat_at),
                    _serialize_dt(job.started_at),
                    _serialize_dt(job.created_at),
                    _serialize_dt(job.updated_at),
                ),
            )
            self._append_event(
                cur,
                job.id,
                "job_created",
                metadata={"job_type": job.type, "correlation_id": job.correlation_id},
            )
        self._conn.commit()
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._conn.cursor() as cur:
            return self._get_job(cur, job_id)

    def find_active_by_idempotency_key(self, idempotency_key: str) -> Job | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM jobs
                WHERE idempotency_key = %s
                  AND status IN (%s, %s, %s)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (
                    idempotency_key,
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.RETRY_SCHEDULED.value,
                ),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_job(row)

    def require_job(self, job_id: str) -> Job:
        with self._conn.cursor() as cur:
            return self._require_job(cur, job_id)

    # ------------------------------------------------------------------
    # Claim — SELECT ... FOR UPDATE SKIP LOCKED
    # ------------------------------------------------------------------

    def claim_next_job(
        self,
        worker_id: str,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
        type_concurrency_limits: dict[str, int] | None = None,
    ) -> Job | None:
        now = datetime.now(UTC)
        now_str = _serialize_dt(now)
        lease_expires = _serialize_dt(now + lease_duration)

        with self._conn.cursor() as cur:
            if type_concurrency_limits:
                # Fetch candidates without LIMIT 1 so we can filter by type
                cur.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = %s
                      AND (run_after IS NULL OR run_after <= %s)
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    """,
                    (JobStatus.PENDING.value, now_str),
                )
                row = None
                for candidate in cur.fetchall():
                    jtype = candidate["type"]
                    limit = type_concurrency_limits.get(jtype)
                    if limit is not None:
                        cur.execute(
                            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = %s AND type = %s",
                            (JobStatus.RUNNING.value, jtype),
                        )
                        running = cur.fetchone()["cnt"]
                        if running >= limit:
                            continue
                    row = candidate
                    break
            else:
                cur.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = %s
                      AND (run_after IS NULL OR run_after <= %s)
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """,
                    (JobStatus.PENDING.value, now_str),
                )
                row = cur.fetchone()

            if row is None:
                self._conn.commit()
                return None

            job = _row_to_job(row)
            validate_transition(job.status, JobStatus.RUNNING)
            updated_at = _serialize_dt(datetime.now(UTC))
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, attempt = attempt + 1, run_after = NULL,
                    leased_by = %s, lease_expires_at = %s, heartbeat_at = %s,
                    started_at = %s, updated_at = %s
                WHERE id = %s AND status = %s
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
                cur,
                job.id,
                "job_claimed",
                metadata={
                    "worker_id": worker_id,
                    "attempt": job.attempt + 1,
                    "lease_expires_at": lease_expires,
                },
            )
        self._conn.commit()
        return self.require_job(job.id)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_succeeded(self, job_id: str) -> Job:
        with self._conn.cursor() as cur:
            job = self._require_job(cur, job_id)
            validate_transition(job.status, JobStatus.SUCCEEDED)
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, last_error = NULL,
                    leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (JobStatus.SUCCEEDED.value, _serialize_dt(datetime.now(UTC)), job_id),
            )
            self._append_event(cur, job_id, "job_succeeded")
        self._conn.commit()
        return self.require_job(job_id)

    def mark_failed(self, job_id: str, error: str) -> Job:
        with self._conn.cursor() as cur:
            job = self._require_job(cur, job_id)
            validate_transition(job.status, JobStatus.FAILED)
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, last_error = %s,
                    leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    JobStatus.FAILED.value,
                    error,
                    _serialize_dt(datetime.now(UTC)),
                    job_id,
                ),
            )
            self._append_event(cur, job_id, "job_failed", message=error)
        self._conn.commit()
        return self.require_job(job_id)

    def mark_retry_scheduled(self, job_id: str, error: str, run_after: datetime) -> Job:
        with self._conn.cursor() as cur:
            job = self._require_job(cur, job_id)
            validate_transition(job.status, JobStatus.RETRY_SCHEDULED)
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, last_error = %s, run_after = %s,
                    leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    JobStatus.RETRY_SCHEDULED.value,
                    error,
                    _serialize_dt(run_after),
                    _serialize_dt(datetime.now(UTC)),
                    job_id,
                ),
            )
            self._append_event(
                cur,
                job_id,
                "retry_scheduled",
                message=error,
                metadata={"attempt": job.attempt, "run_after": _serialize_dt(run_after)},
            )
        self._conn.commit()
        return self.require_job(job_id)

    def mark_dead_lettered(self, job_id: str, error: str) -> Job:
        with self._conn.cursor() as cur:
            job = self._require_job(cur, job_id)
            validate_transition(job.status, JobStatus.DEAD_LETTERED)
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, last_error = %s,
                    leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    JobStatus.DEAD_LETTERED.value,
                    error,
                    _serialize_dt(datetime.now(UTC)),
                    job_id,
                ),
            )
            self._append_event(
                cur,
                job_id,
                "job_dead_lettered",
                message=error,
                metadata={"attempt": job.attempt, "max_attempts": job.max_attempts},
            )
        self._conn.commit()
        return self.require_job(job_id)

    # ------------------------------------------------------------------
    # Promotion & recovery
    # ------------------------------------------------------------------

    def promote_due_retries(self, now: datetime | None = None) -> list[Job]:
        current = _serialize_dt(now or datetime.now(UTC))
        promoted: list[Job] = []
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM jobs
                WHERE status = %s AND run_after IS NOT NULL AND run_after <= %s
                ORDER BY run_after ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                """,
                (JobStatus.RETRY_SCHEDULED.value, current),
            )
            for row in cur.fetchall():
                job = _row_to_job(row)
                validate_transition(job.status, JobStatus.PENDING)
                cur.execute(
                    """
                    UPDATE jobs SET status = %s, run_after = NULL, updated_at = %s
                    WHERE id = %s AND status = %s
                    """,
                    (
                        JobStatus.PENDING.value,
                        _serialize_dt(datetime.now(UTC)),
                        job.id,
                        JobStatus.RETRY_SCHEDULED.value,
                    ),
                )
                self._append_event(cur, job.id, "retry_promoted")
                promoted.append(self._require_job(cur, job.id))
        self._conn.commit()
        return promoted

    def recover_expired_leases(self, now: datetime | None = None) -> list[Job]:
        current = _serialize_dt(now or datetime.now(UTC))
        recovered: list[Job] = []
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM jobs
                WHERE status = %s AND lease_expires_at IS NOT NULL AND lease_expires_at <= %s
                ORDER BY lease_expires_at ASC
                FOR UPDATE SKIP LOCKED
                """,
                (JobStatus.RUNNING.value, current),
            )
            for row in cur.fetchall():
                job = _row_to_job(row)
                target = (
                    JobStatus.PENDING if job.attempt < job.max_attempts
                    else JobStatus.RETRY_SCHEDULED
                )
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = %s, leased_by = NULL, lease_expires_at = NULL,
                        heartbeat_at = NULL, updated_at = %s
                    WHERE id = %s AND status = %s
                    """,
                    (
                        target.value,
                        _serialize_dt(datetime.now(UTC)),
                        job.id,
                        JobStatus.RUNNING.value,
                    ),
                )
                self._append_event(
                    cur,
                    job.id,
                    "lease_expired",
                    message=f"Worker {job.leased_by!r} lease expired",
                    metadata={
                        "worker_id": job.leased_by,
                        "attempt": job.attempt,
                        "recovered_to": target.value,
                    },
                )
                recovered.append(self._require_job(cur, job.id))
        self._conn.commit()
        return recovered

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    ) -> Job:
        with self._conn.cursor() as cur:
            job = self._require_job(cur, job_id)
            if job.status != JobStatus.RUNNING:
                raise JobNotFoundError(
                    f"Cannot heartbeat job {job_id!r} in status {job.status.value!r}"
                )
            if job.leased_by != worker_id:
                raise JobNotFoundError(
                    f"Job {job_id!r} is not leased by worker {worker_id!r}"
                )
            now = datetime.now(UTC)
            cur.execute(
                """
                UPDATE jobs
                SET lease_expires_at = %s, heartbeat_at = %s, updated_at = %s
                WHERE id = %s AND leased_by = %s
                """,
                (
                    _serialize_dt(now + lease_duration),
                    _serialize_dt(now),
                    _serialize_dt(now),
                    job_id,
                    worker_id,
                ),
            )
        self._conn.commit()
        return self.require_job(job_id)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def count_by_status(self) -> dict[str, int]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status")
            return {row["status"]: row["cnt"] for row in cur.fetchall()}

    def count_running_by_type(self, job_type: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM jobs WHERE status = %s AND type = %s",
                (JobStatus.RUNNING.value, job_type),
            )
            row = cur.fetchone()
            return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def append_event(
        self,
        job_id: str,
        event_type: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobEvent:
        with self._conn.cursor() as cur:
            event = self._append_event(cur, job_id, event_type, message, metadata)
        self._conn.commit()
        return event

    def list_events(self, job_id: str | None = None) -> list[JobEvent]:
        with self._conn.cursor() as cur:
            if job_id is None:
                cur.execute("SELECT * FROM job_events ORDER BY created_at ASC")
            else:
                cur.execute(
                    "SELECT * FROM job_events WHERE job_id = %s ORDER BY created_at ASC",
                    (job_id,),
                )
            return [_row_to_event(row) for row in cur.fetchall()]
