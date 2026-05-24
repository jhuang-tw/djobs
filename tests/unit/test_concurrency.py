"""Tests for backlog metrics and per-type concurrency limits."""

from __future__ import annotations

from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository

# ------------------------------------------------------------------
# count_by_status
# ------------------------------------------------------------------


def test_count_by_status_empty(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    assert repo.count_by_status() == {}


def test_count_by_status_reflects_job_states(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("a")
    queue.submit("b")
    queue.submit("c")
    queue.claim("w-1")  # one becomes running

    counts = repo.count_by_status()
    assert counts["pending"] == 2
    assert counts["running"] == 1


def test_backlog_via_queue_service(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("a")
    queue.submit("b")

    backlog = queue.backlog()
    assert backlog["pending"] == 2


# ------------------------------------------------------------------
# count_running_by_type
# ------------------------------------------------------------------


def test_count_running_by_type(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("email")
    queue.submit("email")
    queue.submit("sms")
    queue.claim("w-1")  # claims first email (oldest)

    assert repo.count_running_by_type("email") == 1
    assert repo.count_running_by_type("sms") == 0
    assert queue.count_running_by_type("email") == 1


# ------------------------------------------------------------------
# per-type concurrency limits in claim
# ------------------------------------------------------------------


def test_claim_respects_type_concurrency_limit(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    # Submit 3 email jobs
    queue.submit("email")
    queue.submit("email")
    queue.submit("email")

    # Limit email to 1 concurrent
    limits = {"email": 1}

    # First claim succeeds
    j1 = queue.claim("w-1", type_concurrency_limits=limits)
    assert j1 is not None
    assert j1.type == "email"

    # Second claim should be blocked (1 email already running)
    j2 = queue.claim("w-2", type_concurrency_limits=limits)
    assert j2 is None


def test_claim_skips_limited_type_takes_other(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    # Submit 1 email (will be claimed first), then 1 sms
    queue.submit("email")
    queue.submit("sms")

    limits = {"email": 1}

    j1 = queue.claim("w-1", type_concurrency_limits=limits)
    assert j1 is not None
    assert j1.type == "email"

    # Email at limit, but sms has no limit → should claim sms
    j2 = queue.claim("w-2", type_concurrency_limits=limits)
    assert j2 is not None
    assert j2.type == "sms"


def test_claim_without_limits_ignores_concurrency(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("email")
    queue.submit("email")

    # No limits → both can be claimed
    j1 = queue.claim("w-1")
    j2 = queue.claim("w-2")
    assert j1 is not None
    assert j2 is not None


def test_claim_type_not_in_limits_is_unrestricted(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("sms")
    queue.submit("sms")

    # Only email is limited, sms is unrestricted
    limits = {"email": 1}
    j1 = queue.claim("w-1", type_concurrency_limits=limits)
    j2 = queue.claim("w-2", type_concurrency_limits=limits)
    assert j1 is not None
    assert j2 is not None
