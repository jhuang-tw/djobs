"""Integration test for Phase 3 crash recovery flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def test_expired_lease_recovery_and_rerun(tmp_path) -> None:
    """Simulate worker crash: claim, lease expires, recover, re-claim succeeds."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    registry = HandlerRegistry()
    calls: list[dict[str, Any]] = []

    def handler(payload: dict[str, Any]) -> None:
        calls.append(payload)

    registry.register("demo.echo", handler)
    submitted = queue.submit("demo.echo", {"msg": "hello"}, max_attempts=3)

    # First worker claims with a short lease
    claimed = repo.claim_next_job("worker-crash", lease_duration=timedelta(seconds=1))
    assert claimed is not None

    # Simulate crash: lease expires without heartbeat
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    recovered = queue.recover_expired_leases(now=future)
    assert len(recovered) == 1
    assert recovered[0].status == JobStatus.PENDING
    assert recovered[0].leased_by is None

    # Second worker picks it up and succeeds
    runner = WorkerRunner(queue, registry, worker_id="worker-2")
    result = runner.run_once()
    final = queue.get_job(submitted.id)

    assert result.did_run is True
    assert final is not None
    assert final.status == JobStatus.SUCCEEDED
    assert final.leased_by is None
    assert calls == [{"msg": "hello"}]

    event_types = [e.event_type for e in queue.events(submitted.id)]
    assert event_types == [
        "job_created",
        "job_claimed",
        "lease_expired",
        "job_claimed",
        "job_succeeded",
    ]
