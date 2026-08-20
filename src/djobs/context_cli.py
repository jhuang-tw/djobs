"""Human-readable preview of the repository context djobs can recover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from djobs.project_memory import ProjectMemory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="djobs context",
        description=(
            "Preview the bounded repository context djobs can recover for a coding agent. "
            "This never claims a task."
        ),
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Current task/request used to rank prior repository memory",
    )
    parser.add_argument(
        "--tier",
        choices=("resume", "evidence", "audit"),
        default="resume",
        help="Recovery detail level (default: resume)",
    )
    parser.add_argument("--token-budget", type=int, default=500)
    parser.add_argument("--max-items", type=int, default=6)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _lines(payload: dict[str, Any]) -> list[str]:
    if not payload.get("ok"):
        return [f"djobs context unavailable: {payload.get('error', 'unknown error')}"]
    if payload.get("state") == "empty":
        return [
            f"djobs context — {payload.get('workspace', 'repository')}",
            "No recoverable repository context yet.",
        ]

    output = [f"djobs context — {payload.get('workspace', 'repository')}"]
    resume = payload.get("resume")
    if isinstance(resume, dict):
        goal = resume.get("goal")
        if goal:
            output.append(f"Goal: {goal}")
        constraints = resume.get("constraints")
        if isinstance(constraints, list) and constraints:
            output.append("Constraints:")
            output.extend(f"  - {item}" for item in constraints)
        progress = resume.get("progress")
        if isinstance(progress, list) and progress:
            output.append("Progress:")
            output.extend(f"  - {item}" for item in progress)
        failures = resume.get("failures")
        if isinstance(failures, list) and failures:
            output.append("Failures to avoid repeating:")
            output.extend(f"  - {item}" for item in failures)
        next_step = resume.get("next")
        if next_step:
            output.append(f"Next: {next_step}")
        git_state = resume.get("git")
        if git_state:
            output.append(f"Git: {git_state}")

    tasks = payload.get("tasks")
    if isinstance(tasks, list) and tasks:
        output.append("Explicit work ownership:")
        for item in tasks[:5]:
            if not isinstance(item, dict):
                continue
            summary = item.get("summary") or item.get("id") or "task"
            owner = item.get("owner") or "unclaimed"
            output.append(f"  - {summary} [{item.get('status', 'unknown')}; {owner}]")

    observations = payload.get("observations")
    if isinstance(observations, list) and observations:
        output.append(f"Supporting memories: {len(observations)}")

    next_step = payload.get("next_step")
    if next_step:
        output.append(f"Suggested: {next_step}")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    client = ProjectMemory.open(cwd=str(Path.cwd()), agent_type="djobs-cli")
    raw = client.sync_workspace(
        query=args.query,
        context_tier=args.tier,
        token_budget=args.token_budget,
        max_items=args.max_items,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return 1
    if not isinstance(payload, dict):
        print(raw)
        return 1
    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("\n".join(_lines(payload)))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
