"""Explainable deterministic ranking for passive repository memory."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:+-]{2,}")


@dataclass(frozen=True, slots=True)
class RankedMemory:
    row: dict[str, Any]
    score: float
    matched_by: tuple[str, ...]


def _metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _git_value(root: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_ancestor(root: str, commit: str) -> bool | None:
    if not commit or not Path(root).exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", root, "merge-base", "--is-ancestor", commit, "HEAD"],
            capture_output=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.casefold() for match in _TOKEN_RE.findall(value)))


def rank_memory_rows(
    rows: list[dict[str, Any]],
    *,
    query: str,
    workspace_root: str,
    limit: int,
) -> list[RankedMemory]:
    """Rank rows with stable scores and human-readable reasons.

    The score uses only local deterministic signals. FTS may select candidates,
    but never controls final ordering.
    """

    query_text = " ".join(query.split()).casefold()
    query_terms = _terms(query_text)
    branch = _git_value(workspace_root, "branch", "--show-current")
    head = _git_value(workspace_root, "rev-parse", "HEAD")
    ancestor_cache: dict[str, bool | None] = {}
    ordered = sorted(
        rows,
        key=lambda row: (str(row.get("created_at", "")), str(row.get("id", ""))),
        reverse=True,
    )
    ranked: list[RankedMemory] = []
    seen_summaries: set[str] = set()
    total = max(1, len(ordered))

    for index, row in enumerate(ordered):
        metadata = _metadata(row.get("metadata_json"))
        status = str(metadata.get("memory_status") or "active").casefold()
        if status != "active":
            continue
        summary = " ".join(str(row.get("summary") or "").split())
        normalized_summary = summary.casefold()
        if not normalized_summary or normalized_summary in seen_summaries:
            continue

        haystack = " ".join(
            [
                str(row.get("event_type") or ""),
                str(row.get("tool_name") or ""),
                summary,
                " ".join(str(item) for item in metadata.get("affected_files", []) or []),
            ]
        ).casefold()
        score = 0.0
        reasons: list[str] = []

        if query_text and query_text in haystack:
            score += 4.0
            reasons.append("exact_query")
        overlap = sum(1 for term in query_terms if term in haystack)
        if overlap:
            score += float(overlap)
            reasons.append("query_terms")

        event_type = str(row.get("event_type") or "")
        event_boost = {
            "session_capsule": 2.0,
            "user_intent": 1.6,
            "tool_failure": 1.0,
            "tool_result": 0.6,
            "repository_change": 0.5,
        }.get(event_type, 0.0)
        if event_boost:
            score += event_boost
            reasons.append(f"event:{event_type}")

        row_branch = str(metadata.get("branch") or "")
        if branch and row_branch and row_branch == branch:
            score += 1.5
            reasons.append("same_branch")

        commit = str(metadata.get("commit_sha") or "")
        if commit and head:
            if commit not in ancestor_cache:
                ancestor_cache[commit] = _is_ancestor(workspace_root, commit)
            ancestor = ancestor_cache[commit]
            if ancestor is True:
                score += 1.0
                reasons.append("commit_ancestor")
            elif ancestor is False:
                score -= 1.5
                reasons.append("commit_not_ancestor")

        affected = [
            str(item).casefold() for item in metadata.get("affected_files", []) or [] if str(item)
        ]
        if affected and any(term in path for term in query_terms for path in affected):
            score += 1.2
            reasons.append("affected_path")

        source = str(metadata.get("source") or "")
        source_boost = {
            "user_prompt": 0.8,
            "git_snapshot": 0.5,
            "tool_result": 0.4,
            "agent_summary": 0.2,
        }.get(source, 0.0)
        if source_boost:
            score += source_boost
            reasons.append(f"source:{source}")

        if metadata.get("evidence_id") or metadata.get("source_event_ids"):
            score += 0.4
            reasons.append("linked_evidence")

        freshness = max(0.0, 1.0 - index / total)
        score += freshness
        if freshness >= 0.5:
            reasons.append("recent")

        if query_terms and not overlap and query_text not in haystack:
            continue
        seen_summaries.add(normalized_summary)
        ranked.append(
            RankedMemory(
                row=row,
                score=round(score, 4),
                matched_by=tuple(reasons),
            )
        )

    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[: max(1, limit)]
