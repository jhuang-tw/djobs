"""Tests for the MCP server tool functions (Phase 9).

These test the tool functions directly (without MCP transport) to verify
durable job queue functionality exposed to AI agents.
"""

from __future__ import annotations

import json

import pytest

from djobs.mcp_server import (
    audit_log,
    check_task,
    complete_task,
    configure,
    enqueue_task,
    fail_task,
    health,
    list_tasks,
    resume_session,
)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Configure MCP server with a fresh SQLite DB for each test."""
    db = str(tmp_path / "test_mcp.db")
    configure(db)
    return db


class TestEnqueueTask:
    def test_enqueue_returns_valid_json(self):
        result = json.loads(enqueue_task(task_type="test.job"))
        assert result["type"] == "test.job"
        assert result["status"] == "pending"
        assert result["max_attempts"] == 3  # default

    def test_enqueue_with_payload(self):
        payload = json.dumps({"file": "src/foo.py", "action": "lint"})
        result = json.loads(enqueue_task(task_type="lint", payload=payload))
        assert result["payload"]["file"] == "src/foo.py"

    def test_enqueue_with_correlation_id(self):
        result = json.loads(
            enqueue_task(task_type="test.job", correlation_id="workspace-123")
        )
        assert result["correlation_id"] == "workspace-123"

    def test_enqueue_idempotency(self):
        r1 = json.loads(
            enqueue_task(task_type="lint", idempotency_key="lint:foo.py")
        )
        r2 = json.loads(
            enqueue_task(task_type="lint", idempotency_key="lint:foo.py")
        )
        assert r1["id"] == r2["id"]  # same task, not duplicated

    def test_enqueue_different_keys_create_separate_tasks(self):
        r1 = json.loads(enqueue_task(task_type="lint", idempotency_key="lint:a.py"))
        r2 = json.loads(enqueue_task(task_type="lint", idempotency_key="lint:b.py"))
        assert r1["id"] != r2["id"]


class TestCheckTask:
    def test_check_existing_task(self):
        created = json.loads(enqueue_task(task_type="test.check"))
        result = json.loads(check_task(created["id"]))
        assert result["job_id"] == created["id"]
        assert result["status"] == "pending"
        assert result["type"] == "test.check"

    def test_check_nonexistent_raises(self):
        from djobs.core.errors import JobNotFoundError

        with pytest.raises(JobNotFoundError):
            check_task("nonexistent-id")


class TestCompleteTask:
    """Tests for complete_task MCP tool."""

    def test_complete_pending_task(self):
        """Agent can complete a task directly from pending (no daemon claim)."""
        created = json.loads(enqueue_task(task_type="add-docstrings", correlation_id="ws"))
        result = json.loads(complete_task(created["id"]))
        assert result["status"] == "succeeded"

    def test_complete_running_task(self):
        """Agent can complete a task already claimed (running)."""
        from djobs.mcp_server import _get_queue

        created = json.loads(enqueue_task(task_type="refactor", correlation_id="ws"))
        q = _get_queue()
        q._repository.claim_next_job("some-worker")
        result = json.loads(complete_task(created["id"]))
        assert result["status"] == "succeeded"

    def test_complete_removes_from_resume(self):
        """Completed tasks should NOT appear in resume_session."""
        cid = "complete-resume"
        t1 = json.loads(enqueue_task(task_type="a", correlation_id=cid))
        t2 = json.loads(enqueue_task(task_type="b", correlation_id=cid))
        complete_task(t1["id"])

        result = json.loads(resume_session(cid))
        assert result["incomplete_count"] == 1
        assert result["tasks"][0]["id"] == t2["id"]


class TestFailTask:
    """Tests for fail_task MCP tool."""

    def test_fail_pending_task(self):
        """Agent can fail a task directly from pending."""
        created = json.loads(enqueue_task(task_type="lint", correlation_id="ws"))
        result = json.loads(fail_task(created["id"], "syntax error in target"))
        assert result["status"] == "failed"
        assert result["last_error"] == "syntax error in target"

    def test_fail_shows_in_audit_log(self):
        """Failed tasks should appear in audit_log recent_failures."""
        cid = "fail-audit"
        created = json.loads(enqueue_task(task_type="test-gen", correlation_id=cid))
        fail_task(created["id"], "file not found")

        result = json.loads(audit_log(correlation_id=cid))
        assert result["tasks"]["by_status"].get("failed", 0) >= 1
        errors = [f["error"] for f in result["recent_failures"]]
        assert "file not found" in errors


class TestListTasks:
    def test_list_by_correlation_id(self):
        cid = "batch-42"
        enqueue_task(task_type="a", correlation_id=cid)
        enqueue_task(task_type="b", correlation_id=cid)
        enqueue_task(task_type="c", correlation_id="other")

        result = json.loads(list_tasks(cid))
        assert len(result) == 2
        types = {t["type"] for t in result}
        assert types == {"a", "b"}

    def test_list_with_status_filter(self):
        cid = "filter-test"
        enqueue_task(task_type="x", correlation_id=cid)
        enqueue_task(task_type="y", correlation_id=cid)

        result = json.loads(list_tasks(cid, status_filter="pending"))
        assert len(result) == 2

        result = json.loads(list_tasks(cid, status_filter="succeeded"))
        assert len(result) == 0

    def test_list_empty_correlation(self):
        result = json.loads(list_tasks("nonexistent"))
        assert result == []


class TestResumeSession:
    def test_resume_empty(self):
        result = json.loads(resume_session("fresh-workspace"))
        assert result["incomplete_count"] == 0
        assert "Starting fresh" in result["message"]

    def test_resume_finds_pending_tasks(self):
        cid = "resume-test"
        enqueue_task(task_type="docstring", correlation_id=cid)
        enqueue_task(task_type="docstring", correlation_id=cid)
        enqueue_task(task_type="other", correlation_id="other-cid")

        result = json.loads(resume_session(cid))
        assert result["incomplete_count"] == 2
        assert "2 incomplete" in result["message"]
        assert result["correlation_id"] == cid

    def test_resume_ignores_succeeded(self, tmp_path):
        """Succeeded tasks should NOT appear in resume results."""
        cid = "done-test"
        created = json.loads(enqueue_task(task_type="done", correlation_id=cid))

        # Mark it succeeded via direct queue manipulation
        from djobs.mcp_server import _get_queue

        q = _get_queue()
        repo = q._repository
        # Simulate claim + succeed
        job = repo.get_job(created["id"])
        assert job is not None
        repo.claim_next_job("test-worker")
        repo.mark_succeeded(created["id"])

        result = json.loads(resume_session(cid))
        assert result["incomplete_count"] == 0


class TestHealth:
    def test_health_ok(self):
        result = json.loads(health())
        assert result["status"] == "ok"
        assert "queue_depth" in result
        assert "total_jobs" in result

    def test_health_reflects_enqueued_tasks(self):
        enqueue_task(task_type="a")
        enqueue_task(task_type="b")
        result = json.loads(health())
        assert result["total_jobs"] == 2
        assert result["queue_depth"]["pending"] == 2


class TestCrashRecoveryFlow:
    """Integration test: enqueue → partial process → resume."""

    def test_full_crash_recovery_scenario(self, tmp_path):
        cid = "crash-flow"

        # Enqueue 3 tasks
        ids = []
        for i in range(3):
            r = json.loads(
                enqueue_task(
                    task_type="work",
                    payload=json.dumps({"step": i}),
                    correlation_id=cid,
                )
            )
            ids.append(r["id"])

        # Simulate: claim and complete only the first task
        from djobs.mcp_server import _get_queue

        q = _get_queue()
        repo = q._repository
        claimed = repo.claim_next_job("worker-1")
        assert claimed is not None
        repo.mark_succeeded(claimed.id)

        # "Crash" — now resume
        result = json.loads(resume_session(cid))
        assert result["incomplete_count"] == 2
        assert all(t["status"] == "pending" for t in result["tasks"])


class TestAuditLog:
    """Phase 9.5 — audit_log tool for AI agent action visibility."""

    def _run_one_task(self, *, succeed: bool, cid: str, task_type: str = "work"):
        """Helper: enqueue a task and either succeed or fail it."""
        from djobs.mcp_server import _get_queue

        created = json.loads(
            enqueue_task(task_type=task_type, correlation_id=cid)
        )
        q = _get_queue()
        repo = q._repository
        claimed = repo.claim_next_job(f"worker-{created['id'][:8]}")
        assert claimed is not None
        if succeed:
            repo.mark_succeeded(claimed.id)
        else:
            repo.mark_failed(claimed.id, error="boom")
        return created["id"]

    def test_summary_empty(self):
        result = json.loads(audit_log())
        assert result["total_events"] == 0
        assert result["tasks"]["total"] == 0
        assert result["events_by_type"] == {}
        assert result["recent_failures"] == []

    def test_summary_counts_events_and_tasks(self):
        cid = "audit-summary"
        self._run_one_task(succeed=True, cid=cid, task_type="docstring")
        self._run_one_task(succeed=True, cid=cid, task_type="docstring")
        self._run_one_task(succeed=False, cid=cid, task_type="refactor")

        result = json.loads(audit_log())
        assert result["total_events"] > 0
        assert result["tasks"]["total"] == 3
        assert result["tasks"]["by_type"]["docstring"] == 2
        assert result["tasks"]["by_type"]["refactor"] == 1
        assert result["tasks"]["by_status"]["succeeded"] == 2
        # mark_failed without retry budget => failed (or retry_scheduled if retried)
        assert "job_succeeded" in result["events_by_type"]
        assert "job_failed" in result["events_by_type"]

    def test_summary_recent_failures_include_error_message(self):
        cid = "audit-fail"
        self._run_one_task(succeed=False, cid=cid)
        result = json.loads(audit_log())
        assert len(result["recent_failures"]) == 1
        assert result["recent_failures"][0]["error"] == "boom"
        assert result["recent_failures"][0]["type"] == "work"

    def test_summary_filters_by_correlation_id(self):
        self._run_one_task(succeed=True, cid="cid-a")
        self._run_one_task(succeed=True, cid="cid-b")
        self._run_one_task(succeed=True, cid="cid-b")

        result = json.loads(audit_log(correlation_id="cid-b"))
        assert result["tasks"]["total"] == 2

        result = json.loads(audit_log(correlation_id="cid-a"))
        assert result["tasks"]["total"] == 1

        result = json.loads(audit_log(correlation_id="nonexistent"))
        assert result["tasks"]["total"] == 0

    def test_summary_filters_by_event_type(self):
        cid = "audit-event-type"
        self._run_one_task(succeed=True, cid=cid)
        self._run_one_task(succeed=False, cid=cid)

        result = json.loads(audit_log(event_type="job_failed"))
        assert result["events_by_type"] == {"job_failed": 1}

        result = json.loads(audit_log(event_type="job_succeeded"))
        assert result["events_by_type"] == {"job_succeeded": 1}

    def test_detail_mode_returns_event_list(self):
        cid = "audit-detail"
        self._run_one_task(succeed=True, cid=cid, task_type="docstring")

        result = json.loads(audit_log(summary=False, limit=10))
        assert "events" in result
        assert result["count"] >= 1
        assert all(
            "event_type" in e and "task_type" in e and "at" in e
            for e in result["events"]
        )
        # Most recent event first
        types_in_order = [e["event_type"] for e in result["events"]]
        assert "job_succeeded" in types_in_order

    def test_detail_mode_respects_limit(self):
        cid = "audit-limit"
        for _ in range(3):
            self._run_one_task(succeed=True, cid=cid)

        result = json.loads(audit_log(summary=False, limit=2))
        assert result["count"] == 2
        assert result["truncated"] is True

    def test_since_until_window_excludes_outside_events(self):
        cid = "audit-window"
        self._run_one_task(succeed=True, cid=cid)

        # Window in the far past — should be empty.
        result = json.loads(
            audit_log(
                since="2000-01-01T00:00:00+00:00",
                until="2000-01-02T00:00:00+00:00",
            )
        )
        assert result["total_events"] == 0
        assert result["tasks"]["total"] == 0

    def test_invalid_datetime_returns_error(self):
        result = json.loads(audit_log(since="not-a-date"))
        assert "error" in result
        assert "invalid datetime format" in result["error"]
