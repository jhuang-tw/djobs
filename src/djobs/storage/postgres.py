"""PostgreSQL-backed job repository.

Uses ``psycopg`` (v3) with ``SELECT ... FOR UPDATE SKIP LOCKED`` for
atomic, contention-free job claiming in multi-worker deployments.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from djobs.core.errors import AgentNotFoundError, JobNotFoundError
from djobs.core.models import Agent, Job
from djobs.core.states import AgentStatus, JobStatus, validate_transition
from djobs.storage.events import JobEvent
from djobs.storage.schema import POSTGRES_SCHEMA_SQL, apply_postgres_column_migrations

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError as _exc:
    raise ImportError(
        "psycopg is required for PostgreSQL support. Install it with: pip install 'djobs[pg]'"
    ) from _exc

if TYPE_CHECKING:
    DictConnection = psycopg.Connection[dict[str, Any]]
    DictCursor = psycopg.Cursor[dict[str, Any]]

DEFAULT_LEASE_DURATION = timedelta(seconds=30)
DEFAULT_AGENT_TIMEOUT = timedelta(seconds=90)

# Authoritative schema lives in djobs.storage.schema. Re-exported under the
# historical name for backward compatibility (tests import PG_SCHEMA_SQL).
PG_SCHEMA_SQL = POSTGRES_SCHEMA_SQL


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    # string fallback
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_dt_required(value: Any) -> datetime:
    """Parse a NOT NULL timestamp; raises if the value is unexpectedly NULL."""
    parsed = _parse_dt(value)
    if parsed is None:
        raise ValueError("expected a non-null timestamp")
    return parsed


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
        depends_on=json.loads(row["depends_on_json"]) if row.get("depends_on_json") else [],
        resource_key=row.get("resource_key"),
        created_at=_parse_dt_required(row["created_at"]),
        updated_at=_parse_dt_required(row["updated_at"]),
    )


def _row_to_event(row: dict[str, Any]) -> JobEvent:
    meta = row["metadata_json"]
    return JobEvent(
        id=row["id"],
        job_id=row["job_id"],
        event_type=row["event_type"],
        message=row["message"],
        metadata=json.loads(meta) if meta else {},
        created_at=_parse_dt_required(row["created_at"]),
    )


def _row_to_agent(row: dict[str, Any]) -> Agent:
    return Agent(
        id=row["id"],
        status=AgentStatus(row["status"]),
        capabilities=json.loads(row["capabilities_json"]) if row.get("capabilities_json") else [],
        metadata=json.loads(row["metadata_json"]) if row.get("metadata_json") else {},
        registered_at=_parse_dt_required(row["registered_at"]),
        last_heartbeat_at=_parse_dt_required(row["last_heartbeat_at"]),
    )


class PostgresJobRepository:
    """Repository that persists jobs and lifecycle events in PostgreSQL.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` for contention-free claiming.
    """

    def __init__(self, conn: DictConnection) -> None:
        self._conn = conn

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresJobRepository:
        conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        cls._initialize_schema(conn)
        return cls(conn)

    @staticmethod
    def _initialize_schema(conn: DictConnection) -> None:
        with conn.cursor() as cur:
            cur.execute(PG_SCHEMA_SQL)
            apply_postgres_column_migrations(cur)
        conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _append_event(
        self,
        cur: DictCursor,
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

    def _get_job(self, cur: DictCursor, job_id: str) -> Job | None:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def _require_job(self, cur: DictCursor, job_id: str) -> Job:
        job = self._get_job(cur, job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} was not found")
        return job

    def _dependencies_satisfied(self, cur: DictCursor, row: dict[str, Any]) -> bool:
        """Return True if every dependency of *row* has succeeded."""
        raw = row.get("depends_on_json")
        if not raw:
            return True
        dep_ids = json.loads(raw)
        if not dep_ids:
            return True
        unique_ids = list(set(dep_ids))
        placeholders = ", ".join(["%s"] * len(unique_ids))
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM jobs WHERE id IN ({placeholders}) AND status = %s",
            (*unique_ids, JobStatus.SUCCEEDED.value),
        )
        row_count = cur.fetchone()
        return row_count is not None and row_count["cnt"] == len(unique_ids)

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
                    started_at, depends_on_json, resource_key, created_at, updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    json.dumps(job.depends_on, ensure_ascii=False),
                    job.resource_key,
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
        now = datetime.now(timezone.utc)
        now_str = _serialize_dt(now)
        lease_expires = _serialize_dt(now + lease_duration)

        with self._conn.cursor() as cur:
            # Precompute global aggregates ONCE rather than issuing a subquery
            # per candidate row (avoids an N+1 query pattern):
            #  - running counts per type, for type_concurrency_limits
            #  - the set of resource_keys currently held by RUNNING jobs
            running_by_type: dict[str, int] = {}
            if type_concurrency_limits:
                cur.execute(
                    "SELECT type, COUNT(*) AS cnt FROM jobs WHERE status = %s GROUP BY type",
                    (JobStatus.RUNNING.value,),
                )
                running_by_type = {r["type"]: r["cnt"] for r in cur.fetchall()}
            cur.execute(
                "SELECT DISTINCT resource_key FROM jobs "
                "WHERE status = %s AND resource_key IS NOT NULL",
                (JobStatus.RUNNING.value,),
            )
            held_resource_keys = {r["resource_key"] for r in cur.fetchall()}

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
                if type_concurrency_limits:
                    jtype = candidate["type"]
                    limit = type_concurrency_limits.get(jtype)
                    if limit is not None and running_by_type.get(jtype, 0) >= limit:
                        continue
                if candidate["resource_key"] in held_resource_keys:
                    continue
                if not self._dependencies_satisfied(cur, candidate):
                    continue
                row = candidate
                break

            if row is None:
                self._conn.commit()
                return None

            job = _row_to_job(row)
            validate_transition(job.status, JobStatus.RUNNING)
            updated_at = _serialize_dt(datetime.now(timezone.utc))
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

    def mark_succeeded(self, job_id: str, *, evidence: str | None = None) -> Job:
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
                (JobStatus.SUCCEEDED.value, _serialize_dt(datetime.now(timezone.utc)), job_id),
            )
            metadata = {"evidence": evidence} if evidence else {}
            self._append_event(
                cur,
                job_id,
                "job_succeeded",
                message=evidence,
                metadata=metadata,
            )
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
                    _serialize_dt(datetime.now(timezone.utc)),
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
                    _serialize_dt(datetime.now(timezone.utc)),
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
                    _serialize_dt(datetime.now(timezone.utc)),
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

    def release_job(self, job_id: str, worker_id: str, reason: str | None = None) -> Job:
        """Release a claimed (RUNNING) job back to PENDING.

        Used when an agent voluntarily gives up a task it has claimed (e.g.
        it cannot make progress, or is shutting down gracefully).  The job
        becomes available for another agent to claim immediately.
        """
        with self._conn.cursor() as cur:
            job = self._require_job(cur, job_id)
            if job.status != JobStatus.RUNNING:
                raise JobNotFoundError(
                    f"Cannot release job {job_id!r} in status {job.status.value!r}"
                )
            if job.leased_by != worker_id:
                raise JobNotFoundError(f"Job {job_id!r} is not leased by worker {worker_id!r}")
            validate_transition(job.status, JobStatus.PENDING)
            cur.execute(
                """
                UPDATE jobs
                SET status = %s, last_error = NULL,
                    leased_by = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (JobStatus.PENDING.value, _serialize_dt(datetime.now(timezone.utc)), job_id),
            )
            self._append_event(
                cur,
                job_id,
                "job_released",
                message=reason,
                metadata={"worker_id": worker_id},
            )
        self._conn.commit()
        return self.require_job(job_id)

    # ------------------------------------------------------------------
    # Promotion & recovery
    # ------------------------------------------------------------------

    def promote_due_retries(self, now: datetime | None = None) -> list[Job]:
        current = _serialize_dt(now or datetime.now(timezone.utc))
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
                        _serialize_dt(datetime.now(timezone.utc)),
                        job.id,
                        JobStatus.RETRY_SCHEDULED.value,
                    ),
                )
                self._append_event(cur, job.id, "retry_promoted")
                promoted.append(self._require_job(cur, job.id))
        self._conn.commit()
        return promoted

    def recover_expired_leases(self, now: datetime | None = None) -> list[Job]:
        current = _serialize_dt(now or datetime.now(timezone.utc))
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
                    JobStatus.PENDING
                    if job.attempt < job.max_attempts
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
                        _serialize_dt(datetime.now(timezone.utc)),
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
                raise JobNotFoundError(f"Job {job_id!r} is not leased by worker {worker_id!r}")
            now = datetime.now(timezone.utc)
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

    def list_by_status(self, status: str, limit: int = 100) -> list[Job]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM jobs WHERE status = %s ORDER BY updated_at DESC LIMIT %s",
                (status, limit),
            )
            return [_row_to_job(row) for row in cur.fetchall()]

    def count_stuck_running(self, now: datetime | None = None) -> int:
        """Count running jobs whose lease has expired (stuck tasks)."""
        if now is None:
            now = datetime.now(timezone.utc)
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS cnt FROM jobs
                   WHERE status = %s AND lease_expires_at IS NOT NULL
                   AND lease_expires_at < %s""",
                (JobStatus.RUNNING.value, _serialize_dt(now)),
            )
            row = cur.fetchone()
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
        now = datetime.now(timezone.utc)
        caps_json = json.dumps(list(capabilities or []), ensure_ascii=False)
        meta_json = json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True)
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM agents WHERE id = %s", (agent_id,))
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO agents (
                        id, status, capabilities_json, metadata_json,
                        registered_at, last_heartbeat_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        agent_id,
                        AgentStatus.ONLINE.value,
                        caps_json,
                        meta_json,
                        _serialize_dt(now),
                        _serialize_dt(now),
                    ),
                )
                agent = Agent(
                    id=agent_id,
                    status=AgentStatus.ONLINE,
                    capabilities=list(capabilities or []),
                    metadata=dict(metadata or {}),
                    registered_at=now,
                    last_heartbeat_at=now,
                )
            else:
                agent = _row_to_agent(existing)
                agent.status = AgentStatus.ONLINE
                if capabilities is not None:
                    agent.capabilities = list(capabilities)
                if metadata is not None:
                    agent.metadata = dict(metadata)
                agent.last_heartbeat_at = now
                cur.execute(
                    """
                    UPDATE agents
                    SET status = %s, capabilities_json = %s, metadata_json = %s,
                        last_heartbeat_at = %s
                    WHERE id = %s
                    """,
                    (
                        agent.status.value,
                        json.dumps(agent.capabilities, ensure_ascii=False),
                        json.dumps(agent.metadata, ensure_ascii=False, sort_keys=True),
                        _serialize_dt(agent.last_heartbeat_at),
                        agent_id,
                    ),
                )
        self._conn.commit()
        return agent

    def agent_heartbeat(self, agent_id: str) -> Agent:
        """Record a liveness ping; brings the agent back ONLINE if reaped."""
        now = datetime.now(timezone.utc)
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM agents WHERE id = %s", (agent_id,))
            row = cur.fetchone()
            if row is None:
                raise AgentNotFoundError(f"Agent {agent_id!r} is not registered")
            cur.execute(
                "UPDATE agents SET status = %s, last_heartbeat_at = %s WHERE id = %s",
                (AgentStatus.ONLINE.value, _serialize_dt(now), agent_id),
            )
        self._conn.commit()
        agent = _row_to_agent(row)
        agent.status = AgentStatus.ONLINE
        agent.last_heartbeat_at = now
        return agent

    def get_agent(self, agent_id: str) -> Agent | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM agents WHERE id = %s", (agent_id,))
            row = cur.fetchone()
            return _row_to_agent(row) if row else None

    def list_agents(self, status: str | None = None) -> list[Agent]:
        with self._conn.cursor() as cur:
            if status is None:
                cur.execute("SELECT * FROM agents ORDER BY registered_at ASC")
            else:
                cur.execute(
                    "SELECT * FROM agents WHERE status = %s ORDER BY registered_at ASC",
                    (status,),
                )
            return [_row_to_agent(row) for row in cur.fetchall()]

    def mark_stale_agents_offline(
        self,
        timeout: timedelta = DEFAULT_AGENT_TIMEOUT,
        now: datetime | None = None,
    ) -> list[Agent]:
        """Mark ONLINE agents whose last heartbeat is older than *timeout* OFFLINE."""
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timeout
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE status = %s AND last_heartbeat_at < %s",
                (AgentStatus.ONLINE.value, _serialize_dt(cutoff)),
            )
            rows = cur.fetchall()
            marked: list[Agent] = []
            for row in rows:
                cur.execute(
                    "UPDATE agents SET status = %s WHERE id = %s",
                    (AgentStatus.OFFLINE.value, row["id"]),
                )
                agent = _row_to_agent(row)
                agent.status = AgentStatus.OFFLINE
                marked.append(agent)
        if marked:
            self._conn.commit()
        return marked

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
