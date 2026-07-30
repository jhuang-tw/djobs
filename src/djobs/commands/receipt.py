"""Evidence-backed work receipt shared by CLI and MCP."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from djobs.core.correlation import correlation_id_variants
from djobs.storage.reporting import reporting_repository


def _payload_field(payload_json: str | None, *keys: str) -> str | None:
    if not payload_json:
        return None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _latest_evidence_by_job(repo: Any) -> dict[str, str | None]:
    evidence: dict[str, str | None] = {}
    for row in reporting_repository(repo).latest_success_evidence():
        evidence.setdefault(str(row["job_id"]), row.get("message"))
    return evidence


def build_work_receipt(
    repo: Any,
    correlation_id: str | None,
    *,
    git_root: str | None = None,
) -> dict[str, Any]:
    """Build a read-only, evidence-backed summary of durable work."""

    evidence_by_job = _latest_evidence_by_job(repo)
    correlation_ids = tuple(correlation_id_variants(correlation_id)) if correlation_id else ()
    rows = reporting_repository(repo).receipt_rows(correlation_ids)
    remaining_statuses = {"pending", "running", "retry_scheduled"}
    failed_statuses = {"failed", "dead_lettered"}

    completed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    archived_count = 0
    changed_files: list[str] = []
    seen_files: set[str] = set()
    evidence_present = 0

    for row in rows:
        status = row["status"]
        file = _payload_field(row.get("payload_json"), "file", "path")
        summary = _payload_field(
            row.get("payload_json"), "summary", "title", "name", "description"
        )
        label = summary or file or f"{row['type']} {str(row['id'])[:8]}"
        if status == "succeeded":
            evidence = evidence_by_job.get(str(row["id"]))
            if evidence:
                evidence_present += 1
            completed.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "label": label,
                    "file": file,
                    "evidence": evidence,
                }
            )
            if file and file not in seen_files:
                seen_files.add(file)
                changed_files.append(file)
        elif status in failed_statuses:
            error = row.get("last_error")
            failed.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "label": label,
                    "last_error": error.splitlines()[0] if error else None,
                }
            )
        elif status == "archived":
            archived_count += 1
        elif status in remaining_statuses:
            remaining.append(
                {"id": row["id"], "type": row["type"], "label": label, "status": status}
            )

    if failed:
        next_step = (
            f"Investigate {len(failed)} failed task(s): "
            "`djobs task-history <id>` shows the error and events."
        )
    elif remaining:
        next_step = (
            f"{len(remaining)} task(s) remain. Run `djobs explain` to see why each is "
            "still open, or ask the agent to resume the durable work."
        )
    elif completed:
        next_step = "All tasks complete. Review the changed files (git diff) and commit."
    else:
        next_step = "No tasks recorded for this scope yet."

    receipt: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scope": correlation_id or "all workspaces",
        "totals": {
            "completed": len(completed),
            "remaining": len(remaining),
            "failed": len(failed),
            "archived": archived_count,
        },
        "evidence_coverage": {
            "completed_with_evidence": evidence_present,
            "completed_total": len(completed),
        },
        "changed_files": changed_files,
        "completed_tasks": completed,
        "remaining_tasks": remaining,
        "failed_tasks": failed,
        "recommended_next_step": next_step,
    }
    if git_root:
        from djobs.core.gitinfo import working_tree_changes

        git = working_tree_changes(git_root)
        receipt["git"] = git
        if git.get("is_git_repo") and "changed_files" in git:
            git_files = set(git["changed_files"])
            claimed_not_in_git = [item for item in changed_files if item not in git_files]
            if claimed_not_in_git:
                receipt["claimed_not_in_working_tree"] = claimed_not_in_git
    return receipt
