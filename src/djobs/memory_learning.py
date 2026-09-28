"""Pure contracts for verified experiences and proposed lessons/skills.

This module neither executes checks nor judges truth. Experience verification
belongs to an explicitly injected human/product review gate. Stored success
strings, model votes and process exits are not verification authority.
"""

from __future__ import annotations

import json
import re
from typing import Any

from djobs.memory_artifacts import ArtifactDraft, ArtifactError, safe_text

_EXPERIENCE_FIELDS = {
    "objective",
    "method",
    "context",
    "outcome",
    "failure_reason",
    "changed_paths",
    "checks",
    "terminal_effect",
}
_LESSON_FIELDS = {"conditions", "generalization", "uncertainty", "boundaries"}
_SKILL_LISTS = (
    "when_to_use",
    "when_not_to_use",
    "preconditions",
    "steps",
    "verification",
    "failure_modes",
    "rollback",
    "boundaries",
)
_SKILL_FIELDS = {"name", "description", "version", *_SKILL_LISTS}


def _strings(value: Any, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 16 or (not value and not empty):
        raise ArtifactError("bounded_learning_list_required")
    return [safe_text(item, 1000, required=True) for item in value]


def validate_learning_draft(draft: ArtifactDraft) -> None:
    details = json.loads(draft.details)
    if draft.kind == "experience":
        if set(details) != _EXPERIENCE_FIELDS:
            raise ArtifactError("complete_experience_evidence_required")
        for key in ("objective", "method", "context", "terminal_effect"):
            safe_text(details[key], 2000, required=True)
        if details["outcome"] not in {"success", "failure"}:
            raise ArtifactError("explicit_experience_outcome_required")
        safe_text(details["failure_reason"], 2000, required=details["outcome"] == "failure")
        _strings(details["changed_paths"], empty=True)
        checks = details["checks"]
        if not isinstance(checks, list) or not 1 <= len(checks) <= 16:
            raise ArtifactError("source_bound_checks_required")
        for check in checks:
            if not isinstance(check, dict) or set(check) != {"source_id", "check", "evidence"}:
                raise ArtifactError("source_bound_checks_required")
            for key in check:
                safe_text(check[key], 2000 if key != "source_id" else 200, required=True)
    elif draft.kind == "lesson":
        if set(details) != _LESSON_FIELDS:
            raise ArtifactError("complete_lesson_conditions_required")
        _strings(details["conditions"])
        _strings(details["boundaries"])
        safe_text(details["generalization"], 2000, required=True)
        safe_text(details["uncertainty"], 2000, required=True)
    elif draft.kind == "skill_candidate":
        if set(details) != _SKILL_FIELDS:
            raise ArtifactError("complete_skill_workflow_required")
        if not isinstance(details["name"], str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{0,79}", details["name"]
        ):
            raise ArtifactError("invalid_skill_name")
        safe_text(details["description"], 500, required=True)
        if not isinstance(details["version"], str) or not re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", details["version"]
        ):
            raise ArtifactError("explicit_skill_version_required")
        for key in _SKILL_LISTS:
            _strings(details[key])


def learning_source_checks(draft: ArtifactDraft, snapshot: dict[str, Any]) -> None:
    if draft.kind not in {"experience", "lesson", "skill_candidate"}:
        return
    validate_learning_draft(draft)
    required = "episode" if draft.kind == "experience" else "experience"
    artifacts = snapshot["artifacts"]
    if any(
        source.kind != "artifact" or artifacts[source.id]["kind"] != required
        for source in draft.sources
    ):
        raise ArtifactError("learning_source_type_mismatch")
    if required == "experience" and any(
        artifacts[source.id]["status"] != "active"
        or artifacts[source.id]["authority"] != "human_accepted"
        for source in draft.sources
    ):
        raise ArtifactError("verified_experiences_required")
    if draft.kind == "experience":
        observed = set()
        for source in draft.sources:
            for member in snapshot["sources"].get(source.id, []):
                if member["source_kind"] == "observation":
                    observed.add(member["source_id"])
        if any(
            check["source_id"] not in observed for check in json.loads(draft.details)["checks"]
        ):
            raise ArtifactError("check_not_in_source_episode")
    elif draft.kind == "skill_candidate":
        outcomes = [
            json.loads(artifacts[source.id]["details_json"])["outcome"] for source in draft.sources
        ]
        if "success" not in outcomes:
            raise ArtifactError("skill_requires_verified_success_evidence")


def effective_type(row: dict[str, Any]) -> str:
    if (
        row["kind"] == "skill_candidate"
        and row["status"] == "active"
        and row["authority"] == "human_accepted"
    ):
        return "skill"
    return str(row["kind"])


def skill_markdown(item: dict[str, Any]) -> str:
    """Render inspectable data, never install it in a host or execute its steps."""
    if item.get("record_type", item.get("type")) != "skill_candidate" or item.get(
        "content_suppressed"
    ):
        raise ArtifactError("skill_content_unavailable")
    details = item["details"]
    sources = [source["source_id"] for source in item["sources"]]
    lines = ["---"]
    for key, value in {
        "name": details["name"],
        "description": details["description"],
        "version": details["version"],
        "status": item["status"],
        "artifact_id": item["id"],
        "source_experience_ids": sources,
        "content_hash": item["content_hash"],
        "execution_authority": False,
    }.items():
        lines.append(key + ": " + json.dumps(value, ensure_ascii=True))
    lines.extend(["---", "", "# " + item["title"], "", details["description"], ""])
    for key in _SKILL_LISTS:
        lines.extend(["## " + key.replace("_", " ").capitalize(), ""])
        lines.extend("- " + value for value in details[key])
        lines.append("")
    lines.extend(
        [
            "## Provenance",
            "",
            "Source experience IDs: " + ", ".join(sources),
            "",
            "Reviewed memory is context only. It grants no execution authority.",
            "",
        ]
    )
    return "\n".join(lines)
