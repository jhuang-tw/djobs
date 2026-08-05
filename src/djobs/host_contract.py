"""Versioned advisory-only repository evidence contract."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from djobs import __version__
from djobs.contract_repository import collect_observations, repository_state
from djobs.privacy import redact_text

SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
STATUSES = ("active", "resolved", "superseded", "stale", "contradicted")
RANKING_METHOD = "djobs-observation-rank-v1"


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    query: str | None = None
    task_id: str | None = None
    feature_id: str | None = None
    repository_head: str | None = None
    repository_fingerprint: str | None = None
    kind: str | None = None
    status: str = "active"
    since: str | None = None
    max_age_seconds: int | None = None
    correlation_id: str | None = None
    session_id: str | None = None
    max_items: int = 12
    token_budget: int = 800
    request_id: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _encoded(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("ascii")


def dumps(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, default=str)
    return _encoded(value).decode("ascii")


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_encoded(value)).hexdigest()


def _tokens(value: Any) -> int:
    return max(1, math.ceil(len(_encoded(value)) / 4))


def _clean(value: Any, limit: int) -> str:
    text = " ".join(redact_text(value).replace("\x00", "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _provider(operation: str) -> dict[str, Any]:
    return {
        "name": "djobs",
        "version": __version__,
        "build_commit": os.environ.get("DJOBS_BUILD_COMMIT", "unavailable"),
        "contract": "djobs.host-contract",
        "operation": operation,
    }


def envelope(operation: str, *, generated_at: str | None = None) -> dict[str, Any]:
    return {
        "schema": {"major": SCHEMA_MAJOR, "minor": SCHEMA_MINOR},
        "provider": _provider(operation),
        "operation": operation,
        "authority": "advisory",
        "side_effects": False,
        "writes_database": False,
        "generated_at": generated_at or _iso(_now()),
    }


def capabilities_payload() -> dict[str, Any]:
    read = {
        "read_only": True,
        "side_effecting": False,
        "advisory": True,
        "authoritative": False,
        "claims_task": False,
        "changes_task_status": False,
        "writes_database": False,
        "available_in_advisory_mode": True,
    }
    checkpoint = {
        "read_only": False,
        "side_effecting": True,
        "advisory": False,
        "authoritative": True,
        "claims_task": True,
        "changes_task_status": True,
        "writes_database": True,
        "available_in_advisory_mode": False,
    }
    result = envelope("capabilities")
    result.update(
        {
            "ok": True,
            "mode": "advisory",
            "supported_schema_versions": [
                {"major": 1, "min_minor": 0, "max_minor": SCHEMA_MINOR}
            ],
            "compatibility": {
                "same_major": "additive-only",
                "unknown_fields": "ignore",
                "required_fields": "not_removed_or_retyped_within_major",
                "major_upgrade": "explicit_consumer_opt-in_required",
            },
            "operations": {
                "capabilities": dict(read),
                "observation": dict(
                    read,
                    filters=[
                        "task_id",
                        "feature_id",
                        "repository_head",
                        "repository_fingerprint",
                        "kind",
                        "status",
                        "since",
                        "max_age_seconds",
                        "correlation_id",
                        "session_id",
                    ],
                ),
                "receipt": dict(read),
                "checkpoint": checkpoint,
                "handoff": dict(checkpoint, claims_task=False),
            },
        }
    )
    return result


def error_payload(
    code: str,
    message: str,
    operation: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    result = envelope(operation)
    result.update(
        {
            "ok": False,
            "continue_workflow": True,
            "request_id": request_id or uuid.uuid4().hex,
            "error": {"code": code, "message": _clean(message, 320)},
        }
    )
    return result


def _filters(request: ObservationRequest) -> dict[str, Any]:
    return {
        "query": request.query,
        "task_id": request.task_id,
        "feature_id": request.feature_id,
        "repository_head": request.repository_head,
        "repository_fingerprint": request.repository_fingerprint,
        "kind": request.kind,
        "status": request.status,
        "since": request.since,
        "max_age_seconds": request.max_age_seconds,
        "correlation_id": request.correlation_id,
        "session_id_hash": (
            hashlib.sha256(request.session_id.encode()).hexdigest()[:16]
            if request.session_id
            else None
        ),
        "max_items": max(1, min(request.max_items, 50)),
        "token_budget": max(512, min(request.token_budget, 4000)),
    }


def build_observation_response(
    request: ObservationRequest,
    *,
    cwd: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    started = _now()
    request_id = request.request_id or uuid.uuid4().hex
    try:
        repository = repository_state(cwd)
        if (
            request.repository_fingerprint
            and request.repository_fingerprint != repository["fingerprint"]
        ):
            return error_payload(
                "repository_fingerprint_mismatch",
                "requested repository fingerprint does not match the current checkout",
                "observation",
                request_id,
            )
        if request.repository_head and request.repository_head != repository["head"]:
            return error_payload(
                "repository_head_mismatch",
                "requested repository HEAD does not match the current checkout",
                "observation",
                request_id,
            )
        if request.status not in STATUSES:
            return error_payload(
                "invalid_status", "unsupported memory status", "observation", request_id
            )
        filters = _filters(request)
        candidates = collect_observations(repository, request, started, _hash, db_path)
        limited = candidates[: filters["max_items"]]
        critical = {
            item["id"]
            for item in candidates
            if item["kind"] == "tool_failure"
            and item["status"] == "active"
            and item["repository"]["head"] == repository["head"]
        }
        limited.sort(key=lambda item: (item["id"] not in critical, -item["score"]))
        result = envelope("observation", generated_at=_iso(started))
        result.update(
            {
                "ok": True,
                "continue_workflow": True,
                "mode": "advisory",
                "request_id": request_id,
                "state": "ready" if candidates else "empty",
                "repository": {
                    key: value
                    for key, value in repository.items()
                    if not key.startswith("_")
                },
                "requested_filters": filters,
                "filter_execution": "sqlite_query_stage",
                "repository_observations": limited,
                "budget": {},
            }
        )
        before = _tokens(result)
        selected = list(limited)
        while selected:
            result["repository_observations"] = selected
            result["budget"] = _budget(filters, candidates, limited, selected, critical, before)
            if _tokens(result) <= filters["token_budget"]:
                break
            selected.pop()
        result["repository_observations"] = selected
        result["budget"] = _budget(filters, candidates, limited, selected, critical, before)
        result["receipt"] = _receipt(
            result, filters, repository, candidates, limited, selected, started
        )
        while selected and _tokens(result) > filters["token_budget"]:
            selected.pop()
            result["repository_observations"] = selected
            result["budget"] = _budget(filters, candidates, limited, selected, critical, before)
            result["receipt"] = _receipt(
                result, filters, repository, candidates, limited, selected, started
            )
        for _ in range(3):
            result["budget"]["estimated_selected_tokens"] = _tokens(result)
            result["receipt"]["token_budget"] = dict(result["budget"])
            body = {key: value for key, value in result.items() if key != "receipt"}
            result["receipt"]["output_hash"] = _hash(body)
        return result
    except (OSError, sqlite3.Error, ValueError, subprocess.SubprocessError) as exc:
        return error_payload("provider_unavailable", str(exc), "observation", request_id)


def _budget(filters, candidates, limited, selected, critical, before):
    selected_ids = {item["id"] for item in selected}
    return {
        "requested_tokens": filters["token_budget"],
        "tokens_before_truncation": before,
        "estimated_selected_tokens": 0,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "dropped_for_budget_count": len(limited) - len(selected),
        "truncated": len(selected) < len(candidates),
        "ranking_method": RANKING_METHOD,
        "critical_evidence_omitted": bool(critical - selected_ids),
    }


def _receipt(result, filters, repository, candidates, limited, selected, started):
    return {
        "receipt_kind": "observation_query",
        "request_id": result["request_id"],
        "query_fingerprint": _hash(filters),
        "requested_at": _iso(started),
        "completed_at": _iso(_now()),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "rejected_count": len(candidates) - len(selected),
        "rejection_reasons": {
            "max_items": max(0, len(candidates) - len(limited)),
            "token_budget": len(limited) - len(selected),
        },
        "output_hash": "",
        "hash_scope": "response_without_receipt",
        "provider_version": __version__,
        "provider_build_commit": result["provider"]["build_commit"],
        "repository_fingerprint": repository["fingerprint"],
        "repository_head": repository["head"],
        "token_budget": {},
        "truncated": len(selected) < len(candidates),
    }


def verify_receipt_payload(response: Any) -> dict[str, Any]:
    result = envelope("receipt")
    receipt = response.get("receipt") if isinstance(response, dict) else None
    if not isinstance(receipt, dict):
        result.update(
            {
                "ok": False,
                "continue_workflow": True,
                "valid": False,
                "error": {"code": "missing_receipt", "message": "response has no receipt"},
            }
        )
        return result
    actual = _hash({key: value for key, value in response.items() if key != "receipt"})
    expected = str(receipt.get("output_hash") or "")
    result.update(
        {
            "ok": True,
            "continue_workflow": True,
            "valid": bool(expected) and expected == actual,
            "request_id": receipt.get("request_id"),
            "expected_output_hash": expected,
            "actual_output_hash": actual,
            "receipt": receipt,
        }
    )
    return result
