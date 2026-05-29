"""Tests for the read-only web dashboard (Phase M5)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from djobs.dashboard import build_snapshot, make_server, render_html
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository


def _queue(tmp_path) -> QueueService:
    repo = SQLiteJobRepository.from_path(tmp_path / "dash.db")
    return QueueService(repo)


def test_build_snapshot_empty_queue(tmp_path) -> None:
    queue = _queue(tmp_path)
    snap = build_snapshot(queue)

    assert snap["tasks"] == []
    assert snap["agents"] == []
    assert snap["health"]["total_jobs"] == 0
    assert "generated_at" in snap


def test_build_snapshot_includes_tasks_and_agents(tmp_path) -> None:
    queue = _queue(tmp_path)
    queue.submit("build", resource_key="src/app.py")
    queue.submit("deploy")
    queue.register_agent("agent-1", capabilities=["build"])

    snap = build_snapshot(queue)

    types = {t["type"] for t in snap["tasks"]}
    assert types == {"build", "deploy"}
    assert any(t["resource_key"] == "src/app.py" for t in snap["tasks"])
    assert {a["id"] for a in snap["agents"]} == {"agent-1"}
    assert snap["agents"][0]["status"] == "online"


def test_build_snapshot_reaps_stale_agents(tmp_path) -> None:
    queue = _queue(tmp_path)
    queue.register_agent("ghost")
    # Force the agent stale by reaping with a future 'now'.
    future = datetime.now(UTC) + timedelta(hours=1)
    queue.reap_stale_agents(timeout=timedelta(seconds=1), now=future)

    snap = build_snapshot(queue)
    assert snap["agents"][0]["status"] == "offline"


def test_build_snapshot_shows_running_lease_holder(tmp_path) -> None:
    queue = _queue(tmp_path)
    queue.submit("build")
    claimed = queue.claim("agent-a")
    assert claimed is not None

    snap = build_snapshot(queue)
    running = [t for t in snap["tasks"] if t["status"] == "running"]
    assert len(running) == 1
    assert running[0]["leased_by"] == "agent-a"


def test_render_html_contains_sections(tmp_path) -> None:
    queue = _queue(tmp_path)
    queue.submit("build")
    queue.register_agent("agent-1", capabilities=["build"])
    snap = build_snapshot(queue)

    page = render_html(snap, refresh_seconds=7)

    assert "<!doctype html>" in page
    assert "djobs dashboard" in page
    assert "Agents (1)" in page
    assert "Tasks (1)" in page
    assert 'content="7"' in page  # auto-refresh interval


def test_render_html_escapes_task_type(tmp_path) -> None:
    queue = _queue(tmp_path)
    queue.submit("<script>alert(1)</script>")
    snap = build_snapshot(queue)

    page = render_html(snap)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_render_html_empty_states(tmp_path) -> None:
    queue = _queue(tmp_path)
    page = render_html(build_snapshot(queue))
    assert "No agents registered." in page
    assert "No active tasks." in page


def _serve(queue: QueueService):
    server = make_server(queue, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_http_root_serves_html(tmp_path) -> None:
    queue = _queue(tmp_path)
    queue.submit("build")
    server, thread = _serve(queue)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/html")
        assert "djobs dashboard" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_api_state_serves_json(tmp_path) -> None:
    queue = _queue(tmp_path)
    queue.submit("build")
    queue.register_agent("agent-1")
    server, thread = _serve(queue)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("application/json")
            data = json.loads(resp.read().decode("utf-8"))
        assert data["tasks"][0]["type"] == "build"
        assert data["agents"][0]["id"] == "agent-1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_unknown_path_returns_404(tmp_path) -> None:
    queue = _queue(tmp_path)
    server, thread = _serve(queue)
    try:
        port = server.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope")
            raise AssertionError("expected HTTP 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
