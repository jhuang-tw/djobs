"""Read-only web dashboard — a cross-agent global view of the djobs queue.

Phase M5: a single page that shows queue health, every task, and the live
agent fleet, so an operator no longer needs a per-IDE sidebar to see what all
agents are doing against one shared database.

Implemented with the Python standard library only (``http.server``) — no extra
dependencies.  The dashboard is strictly read-only: it never mutates jobs, it
only reaps stale agents (a liveness maintenance step) when building a snapshot.

Routes
------
- ``GET /``          HTML dashboard (auto-refreshing).
- ``GET /api/state`` JSON snapshot (same data, for tooling / polling).
"""

from __future__ import annotations

import html
import json
import logging
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from djobs.core.models import Agent, Job
from djobs.core.states import JobStatus

if TYPE_CHECKING:
    from djobs.queue.service import QueueService

logger = logging.getLogger(__name__)

# Statuses shown on the dashboard, in display order (archived is excluded).
DASHBOARD_STATUSES: tuple[JobStatus, ...] = (
    JobStatus.RUNNING,
    JobStatus.PENDING,
    JobStatus.RETRY_SCHEDULED,
    JobStatus.FAILED,
    JobStatus.DEAD_LETTERED,
    JobStatus.SUCCEEDED,
)


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status.value,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "correlation_id": job.correlation_id,
        "depends_on": job.depends_on,
        "resource_key": job.resource_key,
        "leased_by": job.leased_by,
        "last_error": job.last_error,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _agent_to_dict(agent: Agent) -> dict[str, Any]:
    return {
        "id": agent.id,
        "status": agent.status.value,
        "capabilities": agent.capabilities,
        "metadata": agent.metadata,
        "registered_at": agent.registered_at.isoformat() if agent.registered_at else None,
        "last_heartbeat_at": (
            agent.last_heartbeat_at.isoformat() if agent.last_heartbeat_at else None
        ),
    }


def build_snapshot(queue: QueueService, *, per_status_limit: int = 50) -> dict[str, Any]:
    """Gather a point-in-time view of the queue for the dashboard.

    Reaps stale agents first so the fleet view reflects who is actually online,
    then collects health, agents, and tasks (grouped by status).
    """
    reaped = queue.reap_stale_agents()
    agents = queue.list_agents()
    health = queue.health()

    tasks: list[dict[str, Any]] = []
    for status in DASHBOARD_STATUSES:
        for job in queue.list_by_status(status.value, limit=per_status_limit):
            tasks.append(_job_to_dict(job))

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "health": health,
        "agents": [_agent_to_dict(a) for a in agents],
        "tasks": tasks,
        "reaped": [a.id for a in reaped],
    }


_STYLE = """
* { box-sizing: border-box; }
body { font: 14px/1.5 system-ui, -apple-system, Segoe UI, sans-serif;
       margin: 0; background: #0f1115; color: #e6e6e6; }
header { padding: 16px 24px; background: #161922; border-bottom: 1px solid #262b36;
         display: flex; align-items: baseline; gap: 16px; }
header h1 { font-size: 18px; margin: 0; }
header .meta { color: #8b93a7; font-size: 12px; }
main { padding: 24px; max-width: 1200px; margin: 0 auto; }
section { margin-bottom: 32px; }
h2 { font-size: 15px; color: #c7cddb; border-bottom: 1px solid #262b36;
     padding-bottom: 6px; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; }
.card { background: #161922; border: 1px solid #262b36; border-radius: 8px;
        padding: 12px 16px; min-width: 110px; }
.card .n { font-size: 22px; font-weight: 600; }
.card .l { font-size: 12px; color: #8b93a7; text-transform: uppercase;
           letter-spacing: .04em; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #20242e; }
th { color: #8b93a7; font-weight: 600; }
td.mono, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px;
         font-size: 11px; font-weight: 600; }
.b-running { background: #14324d; color: #6fb8ff; }
.b-pending { background: #3a3320; color: #e6c46f; }
.b-succeeded { background: #14331f; color: #6fdd95; }
.b-failed, .b-dead_lettered { background: #3a1d1d; color: #ff8a8a; }
.b-retry_scheduled { background: #2c2640; color: #b79bff; }
.b-online { background: #14331f; color: #6fdd95; }
.b-offline { background: #2a2e38; color: #8b93a7; }
.empty { color: #6b7280; font-style: italic; padding: 8px 10px; }
"""


def _badge(value: str) -> str:
    safe = html.escape(value)
    return f'<span class="badge b-{safe}">{safe}</span>'


def _short(value: str | None, length: int = 8) -> str:
    if not value:
        return ""
    return html.escape(value[:length])


def render_html(snapshot: dict[str, Any], *, refresh_seconds: int = 5) -> str:
    """Render the snapshot as a self-contained, auto-refreshing HTML page."""
    health = snapshot.get("health", {})
    depth = health.get("queue_depth", {})
    agents = snapshot.get("agents", [])
    tasks = snapshot.get("tasks", [])
    generated = html.escape(str(snapshot.get("generated_at", "")))

    online = sum(1 for a in agents if a.get("status") == "online")

    cards = [
        ("agents online", f"{online}/{len(agents)}"),
        ("total tasks", str(health.get("total_jobs", 0))),
    ]
    for status in ("running", "pending", "failed", "dead_lettered"):
        if depth.get(status):
            cards.append((status, str(depth[status])))

    card_html = "".join(
        f'<div class="card"><div class="n">{html.escape(n)}</div>'
        f'<div class="l">{html.escape(label)}</div></div>'
        for label, n in cards
    )

    if agents:
        agent_rows = "".join(
            "<tr>"
            f'<td class="mono">{html.escape(a["id"])}</td>'
            f"<td>{_badge(a['status'])}</td>"
            f"<td>{html.escape(', '.join(a.get('capabilities') or []))}</td>"
            f'<td class="mono">{html.escape(str(a.get("last_heartbeat_at") or ""))}</td>'
            "</tr>"
            for a in agents
        )
        agents_table = (
            "<table><thead><tr><th>agent</th><th>status</th>"
            "<th>capabilities</th><th>last heartbeat</th></tr></thead>"
            f"<tbody>{agent_rows}</tbody></table>"
        )
    else:
        agents_table = '<div class="empty">No agents registered.</div>'

    if tasks:
        task_rows = "".join(
            "<tr>"
            f'<td class="mono">{_short(t["id"])}</td>'
            f"<td>{html.escape(t['type'])}</td>"
            f"<td>{_badge(t['status'])}</td>"
            f"<td>{t['attempt']}/{t['max_attempts']}</td>"
            f'<td class="mono">{html.escape(t.get("leased_by") or "")}</td>'
            f'<td class="mono">{html.escape(t.get("resource_key") or "")}</td>'
            f'<td class="mono">{_short(t.get("correlation_id"), 12)}</td>'
            "</tr>"
            for t in tasks
        )
        tasks_table = (
            "<table><thead><tr><th>id</th><th>type</th><th>status</th>"
            "<th>attempt</th><th>leased by</th><th>resource</th>"
            "<th>correlation</th></tr></thead>"
            f"<tbody>{task_rows}</tbody></table>"
        )
    else:
        tasks_table = '<div class="empty">No active tasks.</div>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>djobs dashboard</title>
<style>{_STYLE}</style>
</head>
<body>
<header>
  <h1>djobs dashboard</h1>
  <span class="meta">cross-agent view &middot; generated {generated} &middot;
    auto-refresh {refresh_seconds}s</span>
</header>
<main>
  <section><div class="cards">{card_html}</div></section>
  <section><h2>Agents ({len(agents)})</h2>{agents_table}</section>
  <section><h2>Tasks ({len(tasks)})</h2>{tasks_table}</section>
</main>
</body>
</html>"""


class _DashboardHandler(BaseHTTPRequestHandler):
    """Serves the HTML dashboard and the JSON state endpoint."""

    queue: QueueService  # set on the server instance / class before serving
    refresh_seconds: int = 5

    def _write(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/state":
                snapshot = build_snapshot(self.queue)
                body = json.dumps(snapshot, indent=2, default=str).encode("utf-8")
                self._write(200, "application/json; charset=utf-8", body)
            elif path == "/":
                snapshot = build_snapshot(self.queue)
                body = render_html(snapshot, refresh_seconds=self.refresh_seconds).encode("utf-8")
                self._write(200, "text/html; charset=utf-8", body)
            else:
                self._write(404, "text/plain; charset=utf-8", b"not found")
        except Exception:  # pragma: no cover - defensive: keep server alive
            logger.exception("dashboard request failed")
            self._write(500, "text/plain; charset=utf-8", b"internal error")

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("dashboard %s", format % args)


def make_server(
    queue: QueueService,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    refresh_seconds: int = 5,
) -> ThreadingHTTPServer:
    """Create (but do not start) a dashboard HTTP server bound to *queue*."""
    handler = type(
        "_BoundDashboardHandler",
        (_DashboardHandler,),
        {"queue": queue, "refresh_seconds": refresh_seconds},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve_dashboard(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    refresh_seconds: int = 5,
) -> None:
    """Open the database and serve the dashboard until interrupted."""
    from djobs.queue.service import QueueService
    from djobs.storage.sqlite import SQLiteJobRepository

    queue = QueueService(SQLiteJobRepository.from_path(db_path))
    server = make_server(queue, host, port, refresh_seconds=refresh_seconds)
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Dashboard bound to %s — it exposes queue and agent details with NO "
            "authentication. Only do this on a trusted network; prefer 127.0.0.1 "
            "plus an SSH tunnel for remote access.",
            host,
        )
    logger.info("djobs dashboard listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    finally:
        server.server_close()
