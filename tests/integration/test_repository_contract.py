"""Repository contract tests — must pass for BOTH SQLite and PostgreSQL backends.

Each test is parameterised with a ``repo_factory`` fixture.
SQLite runs in-process; PostgreSQL is skipped if unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from djobs.core.models import Job
from djobs.core.states import JobStatus

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _sqlite_factory(tmp_path):
    from djobs.storage.sqlite import SQLiteJobRepository

    return SQLiteJobRepository.from_path(tmp_path / "contract.db")


def _pg_factory(_tmp_path):
    """Create a PostgresJobRepository, truncating tables between tests."""
    psycopg = pytest.importorskip("psycopg")
    import os

    dsn = os.getenv(
        "DJOBS_TEST_PG_DSN",
        "postgresql://djobs:djobs@localhost:5432/djobs",
    )
    try:
        from psycopg.rows import dict_row

        conn = psycopg.connect(dsn, row_factory=dict_row)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL not available")

    from djobs.storage.postgres import PG_SCHEMA_SQL, PostgresJobRepository

    with conn.cursor() as cur:
        cur.execute(PG_SCHEMA_SQL)
    conn.commit()
    # Truncate for a clean slate
    with conn.cursor() as cur:
        cur.execute("TRUNCATE job_events, jobs CASCADE")
    conn.commit()
    return PostgresJobRepository(conn)


@pytest.fixture(params=["sqlite", "pg"])
def repo(request, tmp_path):
    if request.param == "sqlite":
        return _sqlite_factory(tmp_path)
    return _pg_factory(tmp_path)


# ------------------------------------------------------------------
# Contract tests
# ------------------------------------------------------------------


def test_create_and_get(repo) -> None:
    job = Job(type="demo", payload={"k": "v"}, max_attempts=3)
    created = repo.create_job(job)
    assert created.id == job.id

    fetched = repo.get_job(job.id)
    assert fetched is not None
    assert fetched.type == "demo"
    assert fetched.payload == {"k": "v"}
    assert fetched.status == JobStatus.PENDING
    assert fetched.correlation_id == job.correlation_id


def test_claim_marks_running(repo) -> None:
    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempt == 1
    assert claimed.leased_by == "w-1"
    assert claimed.started_at is not None


def test_claim_returns_none_when_empty(repo) -> None:
    assert repo.claim_next_job("w-1") is None


def test_future_run_after_not_claimed(repo) -> None:
    far = datetime.now(UTC) + timedelta(hours=1)
    repo.create_job(Job(type="a", run_after=far))
    assert repo.claim_next_job("w-1") is None


def test_mark_succeeded(repo) -> None:
    repo.create_job(Job(type="a"))
    repo.claim_next_job("w-1")
    job = repo.get_job(repo.list_events()[0].job_id)
    succeeded = repo.mark_succeeded(job.id)
    assert succeeded.status == JobStatus.SUCCEEDED
    assert succeeded.leased_by is None


def test_mark_failed(repo) -> None:
    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    failed = repo.mark_failed(claimed.id, "oops")
    assert failed.status == JobStatus.FAILED
    assert failed.last_error == "oops"


def test_mark_retry_scheduled(repo) -> None:
    repo.create_job(Job(type="a", max_attempts=3))
    claimed = repo.claim_next_job("w-1")
    run_after = datetime.now(UTC) + timedelta(seconds=10)
    retried = repo.mark_retry_scheduled(claimed.id, "temp", run_after)
    assert retried.status == JobStatus.RETRY_SCHEDULED
    assert retried.run_after is not None


def test_promote_due_retries(repo) -> None:
    repo.create_job(Job(type="a", max_attempts=3))
    claimed = repo.claim_next_job("w-1")
    past = datetime.now(UTC) - timedelta(seconds=10)
    repo.mark_retry_scheduled(claimed.id, "temp", past)

    promoted = repo.promote_due_retries()
    assert len(promoted) == 1
    assert promoted[0].status == JobStatus.PENDING


def test_mark_dead_lettered(repo) -> None:
    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    dl = repo.mark_dead_lettered(claimed.id, "fatal")
    assert dl.status == JobStatus.DEAD_LETTERED


def test_heartbeat(repo) -> None:
    import time

    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    old_expires = claimed.lease_expires_at
    time.sleep(0.01)  # ensure clock advances past claim timestamp
    hb = repo.heartbeat(claimed.id, "w-1")
    assert hb.lease_expires_at > old_expires


def test_recover_expired_leases(repo) -> None:
    repo.create_job(Job(type="a", max_attempts=3))
    repo.claim_next_job("w-crash", lease_duration=timedelta(seconds=1))

    far = datetime.now(UTC) + timedelta(hours=1)
    recovered = repo.recover_expired_leases(far)
    assert len(recovered) == 1
    assert recovered[0].status == JobStatus.PENDING
    assert recovered[0].leased_by is None


def test_idempotency_key_dedupe(repo) -> None:
    j1 = Job(type="a", idempotency_key="ik-1")
    repo.create_job(j1)
    found = repo.find_active_by_idempotency_key("ik-1")
    assert found is not None
    assert found.id == j1.id


def test_count_by_status(repo) -> None:
    repo.create_job(Job(type="a"))
    repo.create_job(Job(type="b"))
    repo.claim_next_job("w-1")
    counts = repo.count_by_status()
    assert counts.get("pending", 0) == 1
    assert counts.get("running", 0) == 1


def test_count_running_by_type(repo) -> None:
    repo.create_job(Job(type="email"))
    repo.create_job(Job(type="sms"))
    repo.claim_next_job("w-1")  # claims email (oldest)
    assert repo.count_running_by_type("email") == 1
    assert repo.count_running_by_type("sms") == 0


def test_type_concurrency_limits(repo) -> None:
    repo.create_job(Job(type="email"))
    repo.create_job(Job(type="email"))
    repo.create_job(Job(type="sms"))

    j1 = repo.claim_next_job("w-1", type_concurrency_limits={"email": 1})
    assert j1 is not None
    assert j1.type == "email"

    # email at limit → should get sms
    j2 = repo.claim_next_job("w-2", type_concurrency_limits={"email": 1})
    assert j2 is not None
    assert j2.type == "sms"


def test_events_recorded(repo) -> None:
    job = Job(type="a")
    repo.create_job(job)
    repo.claim_next_job("w-1")
    repo.mark_succeeded(job.id)

    events = repo.list_events(job.id)
    types = [e.event_type for e in events]
    assert types == ["job_created", "job_claimed", "job_succeeded"]
