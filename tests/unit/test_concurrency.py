"""Tests for backlog metrics and per-type concurrency limits."""

from __future__ import annotations

import threading
from collections import Counter

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


# ------------------------------------------------------------------
# atomic claim under concurrency — no job may be claimed twice
# ------------------------------------------------------------------


def _drain_claims(
    claim_fn,
    claimed: list[str],
    lock: threading.Lock,
    start: threading.Event,
) -> None:
    """Worker loop: claim jobs until the queue is empty, recording each id."""
    start.wait()
    while True:
        job = claim_fn()
        if job is None:
            return
        with lock:
            claimed.append(job.id)


def test_concurrent_claim_shared_repo_no_double_claim(tmp_path) -> None:
    """Many threads on ONE repo (single connection + RLock) never double-claim."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    job_count = 200
    for _ in range(job_count):
        queue.submit("work")

    claimed: list[str] = []
    lock = threading.Lock()
    start = threading.Event()

    worker_count = 8
    threads = [
        threading.Thread(
            target=_drain_claims,
            args=(lambda i=i: queue.claim(f"w-{i}"), claimed, lock, start),
        )
        for i in range(worker_count)
    ]
    for t in threads:
        t.start()
    start.set()  # release all workers at once to maximize contention
    for t in threads:
        t.join()

    # Every job claimed exactly once; none lost, none duplicated.
    assert len(claimed) == job_count
    counts = Counter(claimed)
    duplicates = {jid: n for jid, n in counts.items() if n > 1}
    assert not duplicates, f"jobs claimed more than once: {duplicates}"
    assert len(counts) == job_count
    assert repo.count_by_status().get("running") == job_count


def test_concurrent_claim_separate_connections_no_double_claim(tmp_path) -> None:
    """Threads with SEPARATE connections rely on BEGIN IMMEDIATE for atomicity.

    Each thread opens its own SQLiteJobRepository on the same file, so the
    in-process RLock does NOT protect them — only the database-level write
    lock (``BEGIN IMMEDIATE`` + ``busy_timeout``) prevents double-claiming.
    """
    db_path = tmp_path / "jobs.db"
    seed_repo = SQLiteJobRepository.from_path(db_path)
    seed_queue = QueueService(seed_repo)

    job_count = 120
    for _ in range(job_count):
        seed_queue.submit("work")

    claimed: list[str] = []
    lock = threading.Lock()
    start = threading.Event()

    def worker(worker_id: str) -> None:
        repo = SQLiteJobRepository.from_path(db_path)
        queue = QueueService(repo)
        start.wait()
        while True:
            job = queue.claim(worker_id)
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    worker_count = 6
    threads = [threading.Thread(target=worker, args=(f"w-{i}",)) for i in range(worker_count)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    assert len(claimed) == job_count
    counts = Counter(claimed)
    duplicates = {jid: n for jid, n in counts.items() if n > 1}
    assert not duplicates, f"jobs claimed more than once: {duplicates}"
    assert len(counts) == job_count
    assert seed_repo.count_by_status().get("running") == job_count
