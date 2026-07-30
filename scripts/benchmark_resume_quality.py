#!/usr/bin/env python3
"""Deterministic quality benchmark for djobs context recovery.

Unlike the payload proxy benchmark, this suite measures whether relevant memories
are recalled across sibling worktrees, whether inactive facts stay out of normal
context, and whether a known context hash suppresses identical replay.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from djobs.coding_mcp import _with_context_hash
from djobs.observations import (
    memory_context_hash,
    record_observation,
    search_observations,
    update_observation_status,
)
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_agent_session, resolve_workspace


def _run(*args: str) -> None:
    subprocess.run(list(args), check=True, stdout=subprocess.DEVNULL)


def _fixture(root: Path) -> tuple[Path, Path]:
    primary = root / "primary"
    primary.mkdir()
    _run("git", "init", "-q", str(primary))
    _run("git", "-C", str(primary), "config", "user.name", "djobs benchmark")
    _run(
        "git",
        "-C",
        str(primary),
        "config",
        "user.email",
        "benchmark@example.invalid",
    )
    _run(
        "git",
        "-C",
        str(primary),
        "remote",
        "add",
        "origin",
        "git@github.com:example/recovery-fixture.git",
    )
    (primary / "app.py").write_text("print('base')\n", encoding="utf-8")
    _run("git", "-C", str(primary), "add", ".")
    _run("git", "-C", str(primary), "commit", "-qm", "base")
    sibling = root / "sibling"
    _run("git", "-C", str(primary), "worktree", "add", "-q", "-b", "lane", str(sibling))
    return primary, sibling


def run() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="djobs-resume-quality-") as temp:
        root = Path(temp)
        primary, sibling = _fixture(root)
        repository = SQLiteJobRepository.from_path(root / "memory.db")
        source = resolve_workspace(cwd=str(primary))
        target = resolve_workspace(cwd=str(sibling))
        agent = resolve_agent_session(source, agent_type="codex", session_id="prior-session")

        facts = [
            ("user_intent", "Keep Python 3.10 support; the parser API is public"),
            (
                "tool_failure",
                "OAuth callback failed because state normalization removed plus signs",
            ),
            ("tool_result", "Updated src/parser.py; integration coverage remains"),
            ("user_intent", "Keep Zustand; do not replace the existing store"),
        ]
        for event, summary in facts:
            record_observation(repository, source, agent, event, summary)

        obsolete = search_observations(repository, source, "Zustand store", limit=1)[0]
        update_observation_status(
            repository,
            source,
            obsolete["id"],
            "superseded",
            resolved_by_commit="b" * 40,
        )

        cases = [
            ("parser compatibility", "Python 3.10"),
            ("OAuth callback failure", "plus signs"),
            ("src parser", "src/parser.py"),
        ]
        hits = 0
        for query, expected in cases:
            results = search_observations(repository, target, query, limit=3)
            if any(expected in str(item["summary"]) for item in results):
                hits += 1

        stale_probe = search_observations(repository, target, "Zustand store", limit=3)
        selected = search_observations(repository, target, "continue parser OAuth work", limit=6)
        selected_again = search_observations(
            repository, target, "continue parser OAuth work", limit=6
        )
        context_hash = memory_context_hash(selected)
        replay = json.dumps(
            {
                "ok": True,
                "observations": selected,
                "counts": {"observations": len(selected)},
                "tasks": [],
            }
        )
        unchanged = json.loads(_with_context_hash(replay, context_hash, "evidence"))

        result = {
            "benchmark": "deterministic multi-session recovery quality",
            "worktree_family_match": source.repo_family_id == target.repo_family_id,
            "checkout_isolation": source.checkout_id != target.checkout_id,
            "recall_at_3": round(hits / len(cases), 3),
            "stale_memory_injection_rate": (
                1.0 if any(item["id"] == obsolete["id"] for item in stale_probe) else 0.0
            ),
            "selected_context_items": len(selected),
            "explainable_context_items": sum(
                1 for item in selected if item.get("score") is not None and item.get("matched_by")
            ),
            "deterministic_selection": selected == selected_again,
            "unchanged_replay_items": len(unchanged.get("observations", [])),
            "context_hash_noop": unchanged.get("memory_unchanged") is True,
            "pass": (
                source.repo_family_id == target.repo_family_id
                and source.checkout_id != target.checkout_id
                and hits == len(cases)
                and all(item["id"] != obsolete["id"] for item in stale_probe)
                and selected == selected_again
                and all(item.get("matched_by") for item in selected)
                and unchanged.get("memory_unchanged") is True
                and unchanged.get("observations") == []
            ),
        }
        repository.close()
        return result


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
