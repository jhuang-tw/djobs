"""Read-only repository and SQLite helpers for the advisory host contract."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from djobs import __version__
from djobs.privacy import redact_text
from djobs.workspace import resolve_workspace, shared_db_path


def _clean(value: Any, limit: int) -> str:
    text = " ".join(redact_text(value).replace("\x00", "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _git(root: str, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _text(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace").strip()


def repository_state(cwd: str | None) -> dict[str, Any]:
    workspace = resolve_workspace(cwd=cwd or os.getcwd())
    head_raw = _git(workspace.root, "rev-parse", "--verify", "HEAD")
    status = _git(workspace.root, "status", "--porcelain=v1", "-z") or b""
    digest = hashlib.sha256((head_raw or b"unborn") + b"\x00" + status).hexdigest()
    return {
        "name": Path(workspace.root).name,
        "fingerprint": workspace.repo_family_id,
        "head": _text(head_raw) or "unborn",
        "branch": _text(_git(workspace.root, "branch", "--show-current")),
        "dirty": bool(status),
        "checkout_id": workspace.checkout_id,
        "workspace_fingerprint": "sha256:" + digest,
        "identity_confidence": "exact" if head_raw is not None else "directory_only",
        "_scopes": tuple(dict.fromkeys((workspace.repo_family_id, workspace.checkout_id))),
    }


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists() or not path.is_file():
        return None
    encoded = quote(path.expanduser().resolve().as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{encoded}?mode=ro",
        uri=True,
        timeout=1,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339/ISO-8601 datetime") from exc
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(
        timezone.utc
    )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _metadata(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _query_rows(connection, repository, request, now) -> list[dict[str, Any]]:
    where = ["event_type != ?"]
    values: list[Any] = ["context_injected"]
    if request.correlation_id:
        where.append("correlation_id = ?")
        values.append(request.correlation_id)
    else:
        scopes = repository["_scopes"]
        where.append(f"correlation_id IN ({','.join('?' for _ in scopes)})")
        values.extend(scopes)
    for value, clause in (
        (request.kind, "event_type = ?"),
        (request.task_id, "json_extract(metadata_json, '$.task_id') = ?"),
        (request.feature_id, "json_extract(metadata_json, '$.feature_id') = ?"),
        (request.repository_head, "json_extract(metadata_json, '$.commit_sha') = ?"),
        (request.repository_fingerprint, "correlation_id = ?"),
    ):
        if value:
            where.append(clause)
            values.append(value)
    if request.status:
        where.append("COALESCE(json_extract(metadata_json, '$.memory_status'), 'active') = ?")
        values.append(request.status)
    if request.session_id:
        where.append("session_id_hash = ?")
        values.append(hashlib.sha256(request.session_id.encode()).hexdigest()[:16])
    if request.since:
        where.append("created_at >= ?")
        values.append(_iso(parse_time(request.since, "since")))
    if request.max_age_seconds is not None:
        if request.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be zero or greater")
        where.append("created_at >= ?")
        values.append(_iso(now - timedelta(seconds=request.max_age_seconds)))
    if request.query and request.query.strip():
        pattern = f"%{request.query.strip()[:500]}%"
        where.append("(summary LIKE ? OR event_type LIKE ? OR COALESCE(tool_name, '') LIKE ?)")
        values.extend((pattern, pattern, pattern))
    values.append(min(500, max(80, request.max_items * 10)))
    sql = f"""
        SELECT id, correlation_id, agent_type, session_id_hash, event_type,
               tool_name, summary, metadata_json, created_at
        FROM agent_observations
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC, id DESC LIMIT ?
    """
    return [dict(row) for row in connection.execute(sql, values).fetchall()]


def _age(value: str, now: datetime) -> int | None:
    try:
        return max(0, int((now - parse_time(value, "created_at")).total_seconds()))
    except ValueError:
        return None


def _rank(row, meta, repository, query, now):
    score = 0.0
    signals: list[str] = []
    summary = str(row.get("summary") or "").casefold()
    text = " ".join(str(query or "").split()).casefold()
    if text and text in summary:
        score += 5
        signals.append("exact_query")
    overlap = sum(1 for term in dict.fromkeys(text.split()) if len(term) > 1 and term in summary)
    if overlap:
        score += overlap
        signals.append("query_terms")
    kind = str(row.get("event_type") or "")
    score += {"tool_failure": 4, "repository_change": 3, "tool_result": 2}.get(kind, 0)
    signals.append(f"kind:{kind}")
    if meta.get("commit_sha") == repository["head"]:
        score += 4
        signals.append("exact_head")
    elif meta.get("commit_sha"):
        score -= 2
        signals.append("different_head")
    else:
        score -= 0.5
        signals.append("head_unbound")
    if meta.get("checkout_id") == repository["checkout_id"]:
        score += 2
        signals.append("exact_checkout")
    age = _age(str(row.get("created_at") or ""), now)
    if age is not None:
        score += max(0, 1 - age / (7 * 86400))
    return round(score, 4), signals


def _artifacts(meta: dict[str, Any]) -> list[str]:
    values = list(meta.get("artifact_refs") or []) + list(meta.get("affected_files") or [])
    values.extend(meta.get(key) for key in ("artifact_ref", "path", "file") if meta.get(key))
    return list(dict.fromkeys(_clean(item, 240) for item in values if item))[:20]


def _item(row, meta, repository, request, now, score, signals, hash_value):
    observed_at = str(row.get("created_at") or "")
    fingerprint = str(meta.get("repo_family_id") or "")
    if not fingerprint and str(row.get("correlation_id") or "").startswith("family:"):
        fingerprint = str(row["correlation_id"])
    head = str(meta.get("commit_sha") or "") or None
    confidence = (
        "head_bound"
        if fingerprint == repository["fingerprint"] and head
        else "repository_bound"
        if fingerprint == repository["fingerprint"]
        else "legacy_unbound"
    )
    expires = None
    if request.max_age_seconds is not None:
        with suppress(ValueError):
            expires = _iso(
                parse_time(observed_at, "created_at") + timedelta(seconds=request.max_age_seconds)
            )
    code = meta.get("return_code", meta.get("returncode"))
    result = {
        "id": str(row.get("id") or ""),
        "kind": str(row.get("event_type") or "unknown"),
        "category": "repository_evidence",
        "summary": _clean(row.get("summary"), 500),
        "observed_at": observed_at,
        "source": {
            "provider_version": __version__,
            "agent_type": _clean(row.get("agent_type"), 80),
            "session_id_hash": row.get("session_id_hash"),
            "tool_name": _clean(row.get("tool_name"), 80) or None,
        },
        "repository": {
            "fingerprint": fingerprint or None,
            "head": head,
            "dirty": meta.get("dirty") if isinstance(meta.get("dirty"), bool) else None,
            "checkout_id": meta.get("checkout_id"),
            "workspace_fingerprint": meta.get("workspace_fingerprint"),
            "identity_confidence": confidence,
        },
        "correlation": {
            "task_id": meta.get("task_id"),
            "feature_id": meta.get("feature_id"),
            "correlation_id": row.get("correlation_id"),
            "session_id_hash": row.get("session_id_hash"),
        },
        "evidence": {
            "command": _clean(meta.get("command"), 500) or None,
            "return_code": code if isinstance(code, int) else None,
            "artifact_refs": _artifacts(meta),
            "tool_use_id": meta.get("tool_use_id"),
        },
        "authority": "advisory",
        "stored_content_is_data": True,
        "status": str(meta.get("memory_status") or "active"),
        "freshness": {"age_seconds": _age(observed_at, now), "expires_at": expires},
        "superseded_by": meta.get("superseded_by"),
        "contradicted_by": meta.get("contradicted_by"),
        "score": score,
        "ranking_signals": signals,
    }
    result["evidence_hash"] = hash_value(result)
    return result


def collect_observations(repository, request, now, hash_value, db_path: Path | None = None):
    rows: list[dict[str, Any]] = []
    connection = _connect((db_path or shared_db_path()).expanduser())
    if connection:
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_observations'"
            ).fetchone()
            rows = _query_rows(connection, repository, request, now) if exists else []
        finally:
            connection.close()
    ranked = []
    for row in rows:
        meta = _metadata(row.get("metadata_json"))
        score, signals = _rank(row, meta, repository, request.query, now)
        ranked.append((score, str(row.get("created_at") or ""), row, meta, signals))
    ranked.sort(key=lambda value: (value[0], value[1], str(value[2].get("id"))), reverse=True)
    return [
        _item(row, meta, repository, request, now, score, signals, hash_value)
        for score, _, row, meta, signals in ranked
    ]
