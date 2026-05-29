"""Tests for the MCP server tool functions (Phase 9).

These test the tool functions directly (without MCP transport) to verify
durable job queue functionality exposed to AI agents.
"""

from __future__ import annotations

import json

import pytest

from djobs.core.errors import JobNotFoundError
from djobs.mcp_server import (
    agent_heartbeat,
    audit_log,
    check_task,
    claim_task,
    complete_task,
    configure,
    enqueue_task,
    fail_task,
    health,
    heartbeat_task,
    list_agents,
    list_tasks,
    register_agent,
    release_task,
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
        result = json.loads(enqueue_task(task_type="test.job", correlation_id="workspace-123"))
        assert result["correlation_id"] == "workspace-123"

    def test_enqueue_idempotency(self):
        r1 = json.loads(enqueue_task(task_type="lint", idempotency_key="lint:foo.py"))
        r2 = json.loads(enqueue_task(task_type="lint", idempotency_key="lint:foo.py"))
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

    def test_complete_with_evidence(self):
        """Evidence is stored in audit log when provided."""
        created = json.loads(enqueue_task(task_type="refactor", correlation_id="ws"))
        result = json.loads(
            complete_task(created["id"], evidence="renamed 3 functions in utils.py")
        )
        assert result["status"] == "succeeded"

        # Verify evidence shows up in check_task via inspect
        info = json.loads(check_task(created["id"]))
        assert info["evidence"] == "renamed 3 functions in utils.py"

    def test_complete_without_evidence(self):
        """No evidence field when not provided."""
        created = json.loads(enqueue_task(task_type="lint", correlation_id="ws"))
        complete_task(created["id"])

        info = json.loads(check_task(created["id"]))
        assert "evidence" not in info


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

        created = json.loads(enqueue_task(task_type=task_type, correlation_id=cid))
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
        assert all("event_type" in e and "task_type" in e and "at" in e for e in result["events"])
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


class TestMultiAgentClaim:
    def test_claim_returns_pending_task(self):
        created = json.loads(enqueue_task(task_type="refactor"))
        result = json.loads(claim_task(agent_id="agent-a"))
        assert result["claimed"] is True
        assert result["task"]["id"] == created["id"]
        assert result["task"]["status"] == "running"
        assert result["task"]["leased_by"] == "agent-a"

    def test_claim_empty_queue(self):
        result = json.loads(claim_task(agent_id="agent-a"))
        assert result["claimed"] is False

    def test_claim_respects_task_types_filter(self):
        enqueue_task(task_type="refactor")
        result = json.loads(claim_task(agent_id="agent-a", task_types=["lint"]))
        assert result["claimed"] is False

    def test_two_agents_never_claim_same_task(self):
        enqueue_task(task_type="refactor")
        first = json.loads(claim_task(agent_id="agent-a"))
        second = json.loads(claim_task(agent_id="agent-b"))
        assert first["claimed"] is True
        assert second["claimed"] is False

    def test_heartbeat_renews_lease(self):
        enqueue_task(task_type="refactor")
        claimed = json.loads(claim_task(agent_id="agent-a"))
        task_id = claimed["task"]["id"]
        result = json.loads(heartbeat_task(task_id, "agent-a"))
        assert result["status"] == "running"
        assert result["leased_by"] == "agent-a"

    def test_release_returns_task_to_queue(self):
        enqueue_task(task_type="refactor")
        claimed = json.loads(claim_task(agent_id="agent-a"))
        task_id = claimed["task"]["id"]

        released = json.loads(release_task(task_id, "agent-a", reason="cannot proceed"))
        assert released["status"] == "pending"
        assert released["leased_by"] is None

        reclaimed = json.loads(claim_task(agent_id="agent-b"))
        assert reclaimed["claimed"] is True
        assert reclaimed["task"]["id"] == task_id
        assert reclaimed["task"]["leased_by"] == "agent-b"

    def test_release_rejects_wrong_owner(self):
        enqueue_task(task_type="refactor")
        claimed = json.loads(claim_task(agent_id="agent-a"))
        task_id = claimed["task"]["id"]
        with pytest.raises(JobNotFoundError):
            release_task(task_id, "agent-b")


class TestTaskDependencies:
    def test_enqueue_with_depends_on_records_dependencies(self):
        dep = json.loads(enqueue_task(task_type="build"))
        dependent = json.loads(enqueue_task(task_type="deploy", depends_on=[dep["id"]]))
        assert dependent["depends_on"] == [dep["id"]]

    def test_dependent_task_not_claimable_until_dependency_succeeds(self):
        dep = json.loads(enqueue_task(task_type="build"))
        dependent = json.loads(enqueue_task(task_type="deploy", depends_on=[dep["id"]]))

        # First claim must be the dependency, not the blocked dependent.
        first = json.loads(claim_task(agent_id="agent-a"))
        assert first["claimed"] is True
        assert first["task"]["id"] == dep["id"]

        # Dependent is blocked while the dependency is still running.
        blocked = json.loads(claim_task(agent_id="agent-b"))
        assert blocked["claimed"] is False

        # Complete the dependency → dependent becomes claimable.
        complete_task(dep["id"])
        unblocked = json.loads(claim_task(agent_id="agent-b"))
        assert unblocked["claimed"] is True
        assert unblocked["task"]["id"] == dependent["id"]


class TestResourceLock:
    def test_enqueue_records_resource_key(self):
        task = json.loads(enqueue_task(task_type="edit", resource_key="src/foo.py"))
        assert task["resource_key"] == "src/foo.py"

    def test_same_resource_key_is_locked_while_running(self):
        first = json.loads(enqueue_task(task_type="edit", resource_key="src/foo.py"))
        enqueue_task(task_type="edit", resource_key="src/foo.py")

        claimed = json.loads(claim_task(agent_id="agent-a"))
        assert claimed["claimed"] is True
        assert claimed["task"]["id"] == first["id"]

        # Second task on same resource blocked while first runs.
        blocked = json.loads(claim_task(agent_id="agent-b"))
        assert blocked["claimed"] is False

        # Completing the holder releases the lock.
        complete_task(first["id"])
        unblocked = json.loads(claim_task(agent_id="agent-b"))
        assert unblocked["claimed"] is True
        assert unblocked["task"]["resource_key"] == "src/foo.py"


class TestAgentRegistry:
    def test_register_agent_returns_online(self):
        result = json.loads(
            register_agent(
                agent_id="agent-1",
                capabilities=["build", "deploy"],
                metadata={"host": "box-1"},
            )
        )
        assert result["id"] == "agent-1"
        assert result["status"] == "online"
        assert result["capabilities"] == ["build", "deploy"]
        assert result["metadata"] == {"host": "box-1"}

    def test_agent_heartbeat_keeps_agent_online(self):
        register_agent(agent_id="agent-1")
        result = json.loads(agent_heartbeat(agent_id="agent-1"))
        assert result["status"] == "online"

    def test_list_agents_shows_registered_agents(self):
        register_agent(agent_id="agent-1", capabilities=["build"])
        register_agent(agent_id="agent-2", capabilities=["deploy"])

        result = json.loads(list_agents())
        assert result["count"] == 2
        ids = {a["id"] for a in result["agents"]}
        assert ids == {"agent-1", "agent-2"}

    def test_list_agents_filters_by_status(self):
        register_agent(agent_id="agent-1")
        result = json.loads(list_agents(status="online"))
        assert {a["id"] for a in result["agents"]} == {"agent-1"}

        offline = json.loads(list_agents(status="offline"))
        assert offline["agents"] == []
