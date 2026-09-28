#!/usr/bin/env python3
"""Original offline temporal workflow benchmark, not model or generation accuracy.

Compare the same source-bound claims before/after explicit reviewed relations.
A flat observation log is not falsely scored as supporting historical semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from djobs.artifacts import ArtifactMemory
from djobs.memory_review import ReviewGate
from djobs.observations import forget_observation, record_observation
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="djobs-temporal-benchmark-") as directory:
        root = Path(directory)
        repo = SQLiteJobRepository.from_path(root / "memory.db")
        workspace = Workspace(
            root=str(root),
            workspace_id="repo:temporal",
            checkout_id="repo:temporal",
            repo_family_id="family:temporal",
            correlation_ids=("repo:temporal",),
            memory_correlation_ids=("family:temporal", "repo:temporal"),
            source="synthetic",
        )
        agent = SimpleNamespace(agent_type="fixture", session_id="temporal-fixture")
        memory = ArtifactMemory(repo, workspace)
        gate = ReviewGate(lambda request: "accept", reviewer="synthetic-benchmark-review")
        sources = {}

        def propose(label, text, start, source_id=None):
            if source_id is None:
                record_observation(repo, workspace, agent, "tool_result", text)
                source_id = repo._connection.execute(
                    "SELECT id FROM agent_observations WHERE summary=?", (text,)
                ).fetchone()[0]
                sources[label] = source_id
            payload = {
                "kind": "fact",
                "title": label,
                "abstract": text,
                "sources": [source_id],
                "valid_from": start,
            }
            item = memory.propose(payload)["artifact"]["id"]
            memory.review(item, gate)
            return item, payload

        try:
            old, old_payload = propose(
                "Persistence", "Persistence uses SQLite.", "2020-01-01T00:00:00Z"
            )
            new, _ = propose(
                "Persistence update", "Persistence migrated to PostgreSQL.", "2021-01-01T00:00:00Z"
            )
            child, _ = propose(
                "Dependent", "Historical dependent configuration.", "2020-01-01T00:00:00Z", old
            )
            billing, _ = propose("Billing", "Billing owns deletion.", "2020-01-01T00:00:00Z")
            accounts, _ = propose("Accounts", "Accounts owns deletion.", "2020-01-01T00:00:00Z")
            before = memory.list_artifacts(query="Persistence")
            raw_before = [
                tuple(row)
                for row in repo._connection.execute(
                    "SELECT id,summary FROM agent_observations ORDER BY id"
                )
            ]
            memory.relate(new, old, "supersedes", at="2021-01-01T00:00:00Z", gate=gate)
            memory.relate(billing, accounts, "contradicts", at="2021-01-01T00:00:00Z", gate=gate)
            current = memory.list_artifacts(query="Persistence")
            historical = memory.list_artifacts(at="2020-06-01T00:00:00Z")
            conflict = memory.list_artifacts(query="Billing")
            raw_after = [
                tuple(row)
                for row in repo._connection.execute(
                    "SELECT id,summary FROM agent_observations ORDER BY id"
                )
            ]
            checks = {
                "before_has_two_unrelated_claims": before["count"] == 2,
                "current_replacement_only": [item["id"] for item in current["memories"]] == [new],
                "historical_original_and_dependent": {old, child}
                <= {item["id"] for item in historical["memories"]},
                "conflict_without_winner": conflict["ambiguous"] and not conflict["memories"],
                "raw_evidence_immutable": raw_before == raw_after,
                "repeated_proposal_is_noop": memory.propose(old_payload)["duplicate"],
                "tasks_untouched": repo._connection.execute(
                    "SELECT COUNT(*) FROM jobs"
                ).fetchone()[0]
                == 0,
            }
            latencies, hashes, payloads = [], [], []
            changes = repo._connection.total_changes
            for _ in range(30):
                started = time.perf_counter()
                result = memory.list_artifacts(at="2020-06-01T00:00:00Z", depth=1)
                latencies.append((time.perf_counter() - started) * 1000)
                encoded = json.dumps(result, sort_keys=True, ensure_ascii=False)
                hashes.append(hashlib.sha256(encoded.encode()).hexdigest())
                payloads.append(math.ceil(len(encoded) / 4))
            checks["read_only_replay"] = (
                repo._connection.total_changes == changes and len(set(hashes)) == 1
            )
            forget_observation(repo, workspace, sources["Persistence update"])
            checks["forgotten_source_not_recovered"] = not memory.list_artifacts(
                query="PostgreSQL"
            )["memories"]
            ordered = sorted(latencies)
            return {
                "benchmark": "synthetic-temporal-workflow-v1",
                "schema_version": 1,
                "before": {"unrelated_persistence_claims": before["count"]},
                "after": {
                    "current_persistence_claims": current["count"],
                    "explicit_ambiguity": conflict["ambiguous"],
                },
                "checks": checks,
                "pass": all(checks.values()),
                "retrieval_p50_ms": ordered[len(ordered) // 2],
                "retrieval_p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
                "payload_tokens_max": max(payloads),
                "token_measure": "JSON characters / 4 estimate",
                "external_network_calls": 0,
                "model_calls": 0,
                "generation": "not_run",
                "answer_judge": "not_run",
                "review": "synthetic fixture callback, not real user acceptance",
                "limitation": "Small deterministic workflow, not general memory/model accuracy",
            }
        finally:
            repo.close()


def run_projection(repository=None) -> dict:
    """Measure projection work and payload depths, not avoided database reads."""
    from unittest.mock import patch

    from djobs.artifacts import ArtifactView
    from djobs.memory_artifacts import digest
    from djobs.storage.memory import memory_repository

    with tempfile.TemporaryDirectory(prefix="djobs-projection-benchmark-") as directory:
        root = Path(directory)
        repo = repository or SQLiteJobRepository.from_path(root / "memory.db")
        workspace = Workspace(
            root=str(root),
            workspace_id="repo:projection-benchmark",
            checkout_id="repo:projection-benchmark",
            repo_family_id="family:projection-benchmark",
            correlation_ids=("repo:projection-benchmark",),
            memory_correlation_ids=("family:projection-benchmark", "repo:projection-benchmark"),
            source="synthetic",
        )
        memory = ArtifactMemory(repo, workspace)
        gate = ReviewGate(lambda request: "accept", reviewer="synthetic-projection-fixture")
        try:
            record_observation(
                repo,
                workspace,
                SimpleNamespace(agent_type="fixture", session_id="projection"),
                "tool_result",
                "Synthetic context projection evidence",
            )
            source = memory_repository(repo).scan_rows(
                scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=5
            )[0]["id"]
            for index in range(20):
                item = memory.propose(
                    {
                        "kind": "fact",
                        "title": f"Parser rule {index}",
                        "abstract": "Bounded parser constraint",
                        "overview": "L1 operational context " * 20,
                        "details": {"evidence": "L2 full source detail " * 200},
                        "sources": [source],
                        "valid_from": "2020-01-01T00:00:00Z",
                    }
                )["artifact"]["id"]
                memory.review(item, gate)
            with memory.store.transaction() as cursor:
                before = digest(memory._view(cursor).data)
            sizes = {}
            for depth in (0, 1, 2):
                data = memory.list_artifacts(depth=depth, limit=5)
                sizes[str(depth)] = len(json.dumps(data["memories"], ensure_ascii=False).encode())
            calls = []
            original = ArtifactView.project

            def project(view, artifact_id, **kwargs):
                calls.append(artifact_id)
                return original(view, artifact_id, **kwargs)

            with patch.object(ArtifactView, "project", project):
                selected = memory.tree(limit=5, trace=True)
            first = selected["memories"][0]
            same = memory.get(first["uri"], depth=2)["id"] == first["id"]
            with memory.store.transaction() as cursor:
                after = digest(memory._view(cursor).data)
            checks = {
                "only_selected_nodes_projected": len(calls) == 5,
                "all_candidates_considered": selected["trace"]["candidate_counts"]["eligible"]
                == 20,
                "L0_smaller_than_half_L2": sizes["0"] < sizes["2"] * 0.5,
                "distinct_depths": sizes["0"] < sizes["1"] < sizes["2"],
                "uri_preserves_identity": same,
                "read_only": before == after,
            }
            return {
                "benchmark": "synthetic-context-projection-v1",
                "checks": checks,
                "pass": all(checks.values()),
                "payload_bytes_by_depth": sizes,
                "before_project_every_candidate": 20,
                "after_project_selected": len(calls),
                "storage_loading": "eager bounded source-validating snapshot; not lazy disk IO",
                "model_calls": 0,
                "external_network_calls": 0,
                "generation": "not_run",
                "answer_judge": "not_run",
            }
        finally:
            if repository is None:
                repo.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--projection", action="store_true")
    args = parser.parse_args()
    result = run_projection() if args.projection else run()
    encoded = json.dumps(result, ensure_ascii=True, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
