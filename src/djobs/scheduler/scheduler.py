"""Scheduler loop for delayed-job promotion and lease recovery.

Provides two usage patterns:

1. **tick(now)** — run a single scheduler cycle (unit-test friendly).
2. **run_loop(interval, stop_event)** — run continuously in a thread,
   calling *tick()* at *interval* until *stop_event* is set.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from djobs.queue.service import QueueService

logger = logging.getLogger(__name__)


class TickResult:
    """Outcome of a single scheduler tick."""

    __slots__ = ("errors", "promoted", "reaped", "recovered")

    def __init__(
        self,
        promoted: int = 0,
        recovered: int = 0,
        reaped: int = 0,
        errors: list[Exception] | None = None,
    ) -> None:
        self.promoted = promoted
        self.recovered = recovered
        self.reaped = reaped
        self.errors: list[Exception] = errors if errors is not None else []

    def __repr__(self) -> str:
        return (
            f"TickResult(promoted={self.promoted}, "
            f"recovered={self.recovered}, reaped={self.reaped}, "
            f"errors={len(self.errors)})"
        )


class SchedulerLoop:
    """Drives periodic promote-due-retries and recover-expired-leases calls.

    Parameters
    ----------
    queue:
        A :class:`QueueService` that exposes ``promote_due_retries`` and
        ``recover_expired_leases``.
    """

    def __init__(self, queue: QueueService) -> None:
        self._queue = queue

    # ------------------------------------------------------------------
    # Single tick
    # ------------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> TickResult:
        """Execute one scheduler cycle.

        1. Promote retry-scheduled jobs whose ``run_after`` has passed.
        2. Recover jobs with expired leases (worker crash recovery).
        3. Reap agents that stopped heartbeating (mark them offline).

        Returns a :class:`TickResult` summarising what happened.
        """
        now = now or datetime.now(timezone.utc)
        result = TickResult()

        try:
            promoted = self._queue.promote_due_retries(now)
            result.promoted = len(promoted)
            if promoted:
                logger.info(
                    "Promoted %d due retries",
                    len(promoted),
                    extra={"promoted_ids": [j.id for j in promoted]},
                )
        except Exception as exc:
            logger.exception("Error during promote_due_retries")
            result.errors.append(exc)

        try:
            recovered = self._queue.recover_expired_leases(now)
            result.recovered = len(recovered)
            if recovered:
                logger.info(
                    "Recovered %d expired leases",
                    len(recovered),
                    extra={"recovered_ids": [j.id for j in recovered]},
                )
        except Exception as exc:
            logger.exception("Error during recover_expired_leases")
            result.errors.append(exc)

        try:
            reaped = self._queue.reap_stale_agents(now=now)
            result.reaped = len(reaped)
            if reaped:
                logger.info(
                    "Reaped %d stale agents",
                    len(reaped),
                    extra={"reaped_ids": [a.id for a in reaped]},
                )
        except Exception as exc:
            logger.exception("Error during reap_stale_agents")
            result.errors.append(exc)

        return result

    # ------------------------------------------------------------------
    # Continuous loop
    # ------------------------------------------------------------------

    def run_loop(
        self,
        interval_seconds: float = 5.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Run :meth:`tick` in a loop until *stop_event* is set.

        Parameters
        ----------
        interval_seconds:
            Seconds to sleep between ticks.
        stop_event:
            A :class:`threading.Event` — set it to stop the loop.
            If ``None`` a new event is created (useful for testing).
        """
        if stop_event is None:
            stop_event = threading.Event()

        logger.info(
            "Scheduler loop starting (interval=%.1fs)",
            interval_seconds,
        )

        while not stop_event.is_set():
            self.tick()
            stop_event.wait(timeout=interval_seconds)

        logger.info("Scheduler loop stopped")
