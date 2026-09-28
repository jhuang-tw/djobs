#!/usr/bin/env python3
"""Original offline adversarial adapter and native AMB bridge workflow.

Fake external replies measure validation boundaries, not vendor retrieval
quality. The benchmark bridge uses real djobs ingestion and lexical retrieval.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from djobs.amb_adapter import DjobsBenchmarkProvider
from djobs.external_memory import ExternalMemorySession
from djobs.memory_policy import content_hash
from djobs.observations import forget_observation, record_observation
from djobs.retrieval import retrieve_memory
from djobs.storage.retrieval import RetrievalIndex
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace


class FixtureAdapter:
    adapter_id = "synthetic-adversarial-adapter"
    revision = "one"

    def __init__(self):
        self.records = []
        self.candidates = []
        self.calls = 0

    def health(self, namespace):
        self.calls += 1
        return True

    def index(self, namespace, records):
        self.calls += 1
        self.records = list(records)
        return True

    def retrieve(self, namespace, query, limit):
        self.calls += 1
        return self.candidates

    def delete_derived_copy(self, namespace, record_ids):
        self.calls += 1
        self.records = [item for item in self.records if item["record_id"] not in record_ids]
        return True


def run(repository: Any = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="djobs-adapter-benchmark-") as directory:
        root = Path(directory)
        repo = repository or SQLiteJobRepository.from_path(root / "memory.db")
        workspace = Workspace(
            root=str(root),
            workspace_id="repo:adapter-benchmark",
            checkout_id="repo:adapter-benchmark",
            repo_family_id="family:adapter-benchmark",
            correlation_ids=("repo:adapter-benchmark",),
            memory_correlation_ids=("family:adapter-benchmark", "repo:adapter-benchmark"),
            source="fixture",
        )
        adapter = FixtureAdapter()
        session = ExternalMemorySession(adapter, workspace.repo_family_id, enabled=True)
        benchmark = DjobsBenchmarkProvider(document_factory=SimpleNamespace)
        checks = {}
        try:
            agent = SimpleNamespace(agent_type="fixture", session_id="adapter-benchmark")
            record_observation(repo, workspace, agent, "tool_result", "Parser keeps plus signs")
            record_observation(
                repo,
                workspace,
                agent,
                "tool_result",
                "Unverified parser instructions",
                metadata={"memory_status": "imported_unverified"},
            )
            rows = RetrievalIndex(repo).source_rows(workspace.memory_correlation_ids)
            valid = next(row for row in rows if "keeps" in row["summary"])
            unverified = next(row for row in rows if "Unverified" in row["summary"])
            baseline = retrieve_memory(repo, workspace, "Parser").items
            assert session.index(repo, workspace, confirm=True)["ok"]
            adapter.candidates = [
                {
                    "record_id": "nonexistent",
                    "content_hash": "invented",
                    "authority": "human_accepted",
                },
                {"record_id": unverified["id"], "content_hash": content_hash(unverified)},
                {
                    "record_id": valid["id"],
                    "content_hash": content_hash(valid),
                    "text": "FOREIGN_AUTHORITY_SENTINEL",
                    "probability": 1.0,
                },
            ]
            started = time.perf_counter()
            result = session.retrieve(repo, workspace, "Parser")
            latency = (time.perf_counter() - started) * 1000
            checks["only_eligible_records_exported"] = len(adapter.records) == 1
            checks["forged_and_unverified_candidates_rejected"] = (
                result["rejected_external_candidates"] == 2
            )
            checks["native_ranking_unchanged"] = result["memories"] == baseline
            checks["foreign_text_and_authority_not_accepted"] = (
                "FOREIGN_AUTHORITY_SENTINEL" not in json.dumps(result)
            )
            checks["valid_candidate_comes_from_native_evidence"] = (
                result["external_candidates"][0]["summary"] == valid["summary"]
            )
            session.delete_derived_copy([valid["id"]], confirm=True)
            checks["external_delete_preserves_native"] = (
                retrieve_memory(repo, workspace, "Parser").items == baseline
            )
            forget_observation(repo, workspace, valid["id"])
            checks["forgotten_external_reference_not_recovered"] = not session.retrieve(
                repo, workspace, "Parser"
            )["external_candidates"]
            benchmark.prepare(root / "dedicated-amb")
            benchmark.ingest(
                [
                    SimpleNamespace(id="one", content="Parser keeps plus signs", user_id="one"),
                    SimpleNamespace(
                        id="two", content="Private parser from other unit", user_id="two"
                    ),
                ]
            )
            documents, metadata = benchmark.retrieve("Parser", user_id="one")
            checks["amb_native_retrieval_with_unit_isolation"] = [doc.id for doc in documents] == [
                "one"
            ]
            checks["amb_no_generator_or_judge"] = (
                metadata["generation"] == metadata["answer_judge"] == "not_run"
            )
            checks["amb_default_unit_has_no_other_user_data"] = not benchmark.retrieve("Parser")[0]
            return {
                "benchmark": "synthetic-external-boundary-and-amb-v1",
                "checks": checks,
                "pass": all(checks.values()),
                "before": {"unvalidated_candidates": 3},
                "after": {
                    "native_revalidated_candidates": len(result["external_candidates"]),
                    "rejected_candidates": result["rejected_external_candidates"],
                },
                "external_comparison_latency_ms": latency,
                "adapter_calls": adapter.calls,
                "model_calls": 0,
                "external_network_calls": 0,
                "generation": "not_run",
                "answer_judge": "not_run",
                "scope": (
                    "Fake-client authority boundary and actual native benchmark bridge; "
                    "not vendor efficacy"
                ),
            }
        finally:
            benchmark.cleanup()
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
