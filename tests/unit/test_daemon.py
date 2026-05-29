"""Tests for the Background Daemon (Phase 9.5b)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from djobs.cli import BUILTIN_HANDLERS, _echo_handler, _load_handlers_module, main
from djobs.core.models import Job
from djobs.daemon import Daemon
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_daemon.db")


@pytest.fixture()
def queue(db_path):
    repo = SQLiteJobRepository.from_path(db_path)
    return QueueService(repo)


@pytest.fixture()
def registry():
    reg = HandlerRegistry()
    reg.register("echo", _echo_handler)
    return reg


# ---------------------------------------------------------------------------
# Daemon unit tests
# ---------------------------------------------------------------------------


class TestDaemon:
    def test_from_db_creates_daemon(self, db_path):
        daemon = Daemon.from_db(db_path, handlers={"echo": _echo_handler})
        assert daemon.worker_id.startswith("daemon-")
        assert daemon.pool is not None
        assert daemon.scheduler is not None

    def test_daemon_processes_jobs(self, queue, registry):
        """Daemon should claim and execute enqueued jobs."""
        # Enqueue 3 echo jobs.
        for i in range(3):
            queue.submit(
                job_type="echo",
                payload={"n": i},
                correlation_id="test-daemon",
            )

        daemon = Daemon(
            queue=queue,
            registry=registry,
            max_concurrent=2,
            poll_interval=0.05,
            scheduler_interval=0.5,
        )

        stop = threading.Event()

        def _stop_when_done():
            """Poll until all 3 jobs are done, then stop."""
            for _ in range(200):  # 10s max
                time.sleep(0.05)
                if daemon.pool.completed_count >= 3:
                    stop.set()
                    return
            stop.set()  # timeout safety

        watcher = threading.Thread(target=_stop_when_done, daemon=True)
        watcher.start()
        daemon.run_until(stop)
        watcher.join(timeout=5)

        assert daemon.pool.completed_count == 3
        assert daemon.pool.failed_count == 0

    def test_daemon_stop(self, queue, registry):
        """Calling stop() should terminate run_until()."""
        daemon = Daemon(
            queue=queue,
            registry=registry,
            poll_interval=0.05,
        )
        stop = threading.Event()

        def _run():
            daemon.run_until(stop)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        time.sleep(0.1)
        daemon.stop()
        t.join(timeout=5)
        assert not t.is_alive()

    def test_daemon_handles_failed_jobs(self, queue):
        """Jobs with unknown types are never claimed thanks to type_filter."""
        queue.submit(job_type="unknown_type", payload={}, correlation_id="test")

        registry = HandlerRegistry()
        # Don't register any handler for "unknown_type".
        # With type_filter the daemon won't even claim it.

        daemon = Daemon(
            queue=queue,
            registry=registry,
            max_concurrent=1,
            poll_interval=0.05,
        )
        stop = threading.Event()

        def _stop_after():
            time.sleep(0.3)
            stop.set()

        watcher = threading.Thread(target=_stop_after, daemon=True)
        watcher.start()
        daemon.run_until(stop)
        watcher.join(timeout=5)

        # With type_filter, the job is never claimed → stays pending.
        job = queue.get_job(
            queue._repository._connection.execute(
                "SELECT id FROM jobs WHERE type = 'unknown_type'"
            ).fetchone()[0]
        )
        assert job is not None
        assert job.status.value == "pending"
        assert daemon.pool.failed_count == 0

    def test_daemon_skips_ai_tasks(self, queue, registry):
        """Daemon with 'echo' handler must NOT claim 'add-docstrings' tasks."""
        # Enqueue an AI-managed task and a daemon-managed task.
        queue.submit(job_type="add-docstrings", payload={"file": "foo.py"}, correlation_id="test")
        queue.submit(job_type="echo", payload={"msg": "hi"}, correlation_id="test")

        daemon = Daemon(
            queue=queue,
            registry=registry,
            max_concurrent=2,
            poll_interval=0.05,
        )
        stop = threading.Event()

        def _stop_when_echo_done():
            for _ in range(200):
                time.sleep(0.05)
                if daemon.pool.completed_count >= 1:
                    stop.set()
                    return
            stop.set()

        watcher = threading.Thread(target=_stop_when_echo_done, daemon=True)
        watcher.start()
        daemon.run_until(stop)
        watcher.join(timeout=5)

        # Echo job completed, AI task untouched.
        assert daemon.pool.completed_count == 1
        assert daemon.pool.failed_count == 0

        # Verify the AI task is still pending.
        with queue._repository._lock:
            row = queue._repository._connection.execute(
                "SELECT status FROM jobs WHERE type = 'add-docstrings'"
            ).fetchone()
        assert row["status"] == "pending"

    def test_daemon_scheduler_promotes_retries(self, db_path):
        """Scheduler thread should promote retry-scheduled jobs."""
        from datetime import UTC, datetime, timedelta

        repo = SQLiteJobRepository.from_path(db_path)
        q = QueueService(repo)

        # Submit, claim, then trigger a retryable failure.
        q.submit(
            job_type="echo",
            payload={},
            max_attempts=3,
            correlation_id="retry-test",
        )
        claimed = q.claim("w1")
        assert claimed is not None
        # Mark as retryable — this sets run_after in the past for fast testing.
        retried = q.retry_or_dead_letter(
            claimed.id,
            error="transient",
            now=datetime.now(UTC) - timedelta(seconds=10),
        )
        assert retried.status.value == "retry_scheduled"

        registry = HandlerRegistry()
        registry.register("echo", _echo_handler)

        daemon = Daemon(
            queue=q,
            registry=registry,
            max_concurrent=1,
            poll_interval=0.05,
            scheduler_interval=0.1,
        )
        stop = threading.Event()

        def _stop_when_done():
            for _ in range(200):
                time.sleep(0.05)
                if daemon.pool.completed_count >= 1:
                    stop.set()
                    return
            stop.set()

        watcher = threading.Thread(target=_stop_when_done, daemon=True)
        watcher.start()
        daemon.run_until(stop)
        watcher.join(timeout=5)

        # Scheduler should have promoted the retry → pool executed it.
        assert daemon.pool.completed_count >= 1


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_echo_handler_returns_payload(self):
        result = _echo_handler({"msg": "hi"})
        assert result == {"echoed": {"msg": "hi"}}

    def test_builtin_handlers_include_echo(self):
        assert "echo" in BUILTIN_HANDLERS

    def test_load_handlers_module_missing_handlers_dict(self, tmp_path, monkeypatch):
        """Module without HANDLERS should raise ImportError."""
        mod_file = tmp_path / "bad_mod.py"
        mod_file.write_text("X = 1\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(ImportError, match="does not export a HANDLERS dict"):
            _load_handlers_module("bad_mod")

    def test_load_handlers_module_ok(self, tmp_path, monkeypatch):
        """Module with HANDLERS dict should load correctly."""
        mod_file = tmp_path / "good_mod.py"
        mod_file.write_text("def _h(p): return p\nHANDLERS = {'custom': _h}\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        handlers = _load_handlers_module("good_mod")
        assert "custom" in handlers

    def test_main_no_command_exits(self):
        """Running with no subcommand should exit with code 1."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    def test_status_outputs_json_for_workspace(self, tmp_path, capsys):
        """status command returns queue health and correlation-scoped tasks."""
        db_path = tmp_path / "jobs.db"
        repository = SQLiteJobRepository.from_path(db_path)
        repository.create_job(
            Job(
                type="add-docstrings",
                payload={"file": "src/example.py"},
                correlation_id="workspace-a",
            )
        )
        repository.create_job(
            Job(
                type="other-task",
                payload={"file": "src/other.py"},
                correlation_id="workspace-b",
            )
        )

        main(["status", "--db", str(db_path), "--correlation-id", "workspace-a"])

        output = json.loads(capsys.readouterr().out)
        assert "timestamp" in output
        assert output["health"]["status"] == "ok"
        assert len(output["tasks"]) == 1
        assert output["tasks"][0]["type"] == "add-docstrings"
        assert output["tasks"][0]["correlation_id"] == "workspace-a"
        assert json.loads(output["tasks"][0]["payload_json"]) == {"file": "src/example.py"}

    def test_status_includes_latest_evidence(self, tmp_path, capsys):
        db_path = tmp_path / "jobs.db"
        repository = SQLiteJobRepository.from_path(db_path)
        queue = QueueService(repository)
        job = queue.submit(
            "add-docstrings",
            {"file": "src/example.py"},
            correlation_id="workspace-a",
        )
        queue.complete(job.id, evidence="Added 3 function docstrings")

        main(["status", "--db", str(db_path), "--correlation-id", "workspace-a"])

        output = json.loads(capsys.readouterr().out)
        assert output["tasks"][0]["evidence"] == "Added 3 function docstrings"

    def test_skip_marks_task_succeeded_with_evidence(self, tmp_path, capsys):
        db_path = tmp_path / "jobs.db"
        repository = SQLiteJobRepository.from_path(db_path)
        job = repository.create_job(
            Job(
                type="add-docstrings",
                payload={"file": "src/example.py"},
                correlation_id="workspace-a",
            )
        )

        main(["skip", job.id, "--db", str(db_path), "--evidence", "Reviewed manually"])

        output = json.loads(capsys.readouterr().out)
        stored = repository.require_job(job.id)
        assert output["status"] == "succeeded"
        assert stored.status.value == "succeeded"
        assert repository.list_events(job.id)[-1].message == "Reviewed manually"

    def test_accept_before_marks_earlier_tasks_succeeded(self, tmp_path, capsys):
        db_path = tmp_path / "jobs.db"
        repository = SQLiteJobRepository.from_path(db_path)
        first = repository.create_job(Job(type="a", correlation_id="workflow-a"))
        second = repository.create_job(Job(type="b", correlation_id="workflow-a"))
        third = repository.create_job(Job(type="c", correlation_id="workflow-a"))

        main(["accept-before", third.id, "--db", str(db_path), "--evidence", "Accepted in bulk"])

        output = json.loads(capsys.readouterr().out)
        assert output["count"] == 2
        assert repository.require_job(first.id).status.value == "succeeded"
        assert repository.require_job(second.id).status.value == "succeeded"
        assert repository.require_job(third.id).status.value == "pending"

    def test_archive_workflow_archives_only_non_terminal_tasks(self, tmp_path, capsys):
        db_path = tmp_path / "jobs.db"
        repository = SQLiteJobRepository.from_path(db_path)
        queue = QueueService(repository)
        active = queue.submit("a", correlation_id="workflow-a")
        done = queue.submit("b", correlation_id="workflow-a")
        queue.complete(done.id, evidence="done")

        main(["archive-workflow", "--db", str(db_path), "--correlation-id", "workflow-a"])

        output = json.loads(capsys.readouterr().out)
        assert output["count"] == 1
        assert repository.require_job(active.id).status.value == "archived"
        assert repository.require_job(done.id).status.value == "succeeded"


# ---------------------------------------------------------------------------
# Integration: MCP enqueue → Daemon processes
# ---------------------------------------------------------------------------


class TestMCPDaemonIntegration:
    """End-to-end: enqueue via MCP tool → daemon picks up and completes."""

    def test_mcp_enqueue_daemon_execute(self, db_path):
        from djobs.mcp_server import configure, enqueue_task

        # MCP server writes to the same DB.
        configure(db_path)

        # Enqueue via MCP tool (same as Copilot Chat would).
        result = json.loads(
            enqueue_task(
                task_type="echo",
                payload=json.dumps({"test": "integration"}),
                correlation_id="mcp-daemon-test",
            )
        )
        job_id = result["id"]
        assert result["status"] == "pending"

        # Daemon reads from the same DB.
        daemon = Daemon.from_db(
            db_path,
            handlers={"echo": _echo_handler},
            max_concurrent=1,
            poll_interval=0.05,
        )
        stop = threading.Event()

        def _stop_when_done():
            for _ in range(200):
                time.sleep(0.05)
                if daemon.pool.completed_count >= 1:
                    stop.set()
                    return
            stop.set()

        watcher = threading.Thread(target=_stop_when_done, daemon=True)
        watcher.start()
        daemon.run_until(stop)
        watcher.join(timeout=5)

        assert daemon.pool.completed_count == 1

        # Verify via MCP check_task.
        from djobs.mcp_server import check_task

        status = json.loads(check_task(job_id))
        assert status["status"] == "succeeded"
