#!/usr/bin/env python3
"""Original synthetic learning workflow, using explicit fixture review callbacks.

Measures supported transitions and retained provenance, not model accuracy or a
real user's acceptance. No model, network, process executor or prompt installer.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from djobs.artifacts import ArtifactMemory
from djobs.memory_review import ReviewGate
from djobs.observations import forget_observation, record_observation
from djobs.storage.memory import memory_repository
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace


def run(repository: Any = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="djobs-learning-benchmark-") as directory:
        root = Path(directory)
        repo = repository or SQLiteJobRepository.from_path(root / "memory.db")
        workspace = Workspace(
            root=str(root),
            workspace_id="repo:learning-benchmark",
            checkout_id="repo:learning-benchmark",
            repo_family_id="family:learning-benchmark",
            correlation_ids=("repo:learning-benchmark",),
            memory_correlation_ids=("family:learning-benchmark", "repo:learning-benchmark"),
            source="synthetic",
        )
        memory = ArtifactMemory(repo, workspace)
        agent = SimpleNamespace(agent_type="fixture", session_id="learning-benchmark")
        accept = ReviewGate(lambda request: "accept", reviewer="synthetic-benchmark-review")
        reject = ReviewGate(lambda request: "reject", reviewer="synthetic-benchmark-review")
        checks: dict[str, bool] = {}
        experiences, raw_ids, ingestion_ms = [], [], []
        try:
            before = memory.list_artifacts(kind="skill")["count"]
            for index in range(2):
                text = f"Synthetic verified check {index}: callback state preserves plus signs."
                record_observation(repo, workspace, agent, "tool_result", text)
                rows = memory_repository(repo).scan_rows(
                    scopes=workspace.memory_correlation_ids,
                    marker_event="context_injected",
                    limit=10,
                )
                raw_id = next(row["id"] for row in rows if row["summary"] == text)
                raw_ids.append(raw_id)
                episode = memory.episode([raw_id])["artifact"]["id"]
                payload = {
                    "kind": "experience",
                    "title": f"Reviewed recovery {index}",
                    "abstract": text,
                    "sources": [episode],
                    "details": {
                        "objective": "Recover callback parser",
                        "method": "Preserve plus signs",
                        "context": "Original synthetic coding fixture",
                        "outcome": "success",
                        "failure_reason": "",
                        "changed_paths": ["src/parser.py"],
                        "checks": [
                            {
                                "source_id": raw_id,
                                "check": "focused parser check",
                                "evidence": text,
                            }
                        ],
                        "terminal_effect": "Source-bound successful check, no task ownership",
                    },
                }
                preview = memory.experience(payload)
                checks[f"preview_{index}_does_not_verify"] = not preview["verified"]
                declined = memory.experience(payload, reject)
                checks[f"reject_{index}_does_not_create"] = not declined["verified"]
                started = time.perf_counter()
                verified = memory.experience(payload, accept)
                ingestion_ms.append((time.perf_counter() - started) * 1000)
                experiences.append(verified["artifact"]["id"])
                checks[f"verified_{index}_has_receipt"] = (
                    verified["verified"] and not verified["receipt"]["execution_authority"]
                )
                checks[f"duplicate_{index}_is_noop"] = memory.experience(payload, accept)[
                    "duplicate"
                ]
            lesson = memory.propose(
                {
                    "kind": "lesson",
                    "title": "Preserve encoded callback state",
                    "abstract": "Candidate generalization from two reviewed experiences.",
                    "sources": experiences,
                    "details": {
                        "conditions": ["Same callback encoding contract"],
                        "generalization": "Do not normalize plus into whitespace before decoding",
                        "uncertainty": "Two examples do not establish a universal guarantee",
                        "boundaries": ["No automatic execution or activation"],
                    },
                }
            )["artifact"]
            checks["lesson_stays_candidate"] = (
                lesson["status"] == "candidate"
                and not memory.list_artifacts(kind="lesson")["memories"]
            )
            skill = memory.propose(
                {
                    "kind": "skill_candidate",
                    "title": "Callback recovery",
                    "abstract": "Source-bound workflow",
                    "sources": experiences,
                    "details": {
                        "name": "callback-recovery",
                        "description": "Preserve encoded callback state",
                        "version": "1.0.0",
                        "when_to_use": ["Matching parser failure"],
                        "when_not_to_use": ["No matching source evidence"],
                        "preconditions": ["Review sources"],
                        "steps": ["Preserve plus signs during decoding"],
                        "verification": ["Run focused check"],
                        "failure_modes": ["A different encoding contract"],
                        "rollback": ["Revert the bounded diff"],
                        "boundaries": ["No ownership or execution authority"],
                    },
                }
            )["artifact"]
            checks["skill_candidate_not_active"] = not memory.list_artifacts(kind="skill")[
                "memories"
            ]
            preview = memory.review(skill["id"])
            checks["review_preview_not_active"] = not preview["activated"]
            accepted = memory.review(skill["id"], accept)
            final = memory.get(skill["id"], depth=2)
            checks["accepted_skill_is_explicit"] = (
                accepted["activated"] and final["type"] == "skill"
            )
            checks["candidate_content_identity_preserved"] = (
                final["content_hash"] == skill["content_hash"]
            )
            checks["human_readable_workflow"] = all(
                heading in final["markdown"]
                for heading in (
                    "## Preconditions",
                    "## Verification",
                    "## Boundaries",
                    "## Provenance",
                )
            )
            after = memory.list_artifacts(kind="skill")["count"]
            latencies = []
            for _ in range(20):
                started = time.perf_counter()
                memory.list_artifacts(kind="skill")
                latencies.append((time.perf_counter() - started) * 1000)
            with memory.store.transaction() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM jobs")
                checks["tasks_untouched"] = cursor.fetchone()["n"] == 0
            forget_observation(repo, workspace, raw_ids[0])
            checks["forgotten_joint_source_suppresses_skill"] = not memory.list_artifacts(
                kind="skill", exposure="audit"
            )["memories"]
            ordered = sorted(latencies)
            return {
                "benchmark": "synthetic-verified-learning-v1",
                "schema_version": 1,
                "before": {"active_skills": before},
                "after": {"active_skills": after},
                "checks": checks,
                "pass": all(checks.values()),
                "experience_ingestion_p50_ms": sorted(ingestion_ms)[len(ingestion_ms) // 2],
                "retrieval_p50_ms": ordered[len(ordered) // 2],
                "retrieval_p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
                "markdown_bytes": len(final["markdown"].encode()),
                "model_calls": 0,
                "external_network_calls": 0,
                "generation": "not_run",
                "answer_judge": "not_run",
                "review": "synthetic explicit callbacks, not actual human acceptance",
                "scope": "Verified transition contract; no model/general accuracy claim",
            }
        finally:
            if repository is None:
                repo.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    encoded = json.dumps(result, ensure_ascii=True, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
