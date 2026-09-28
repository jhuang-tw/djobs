#!/usr/bin/env python3
"""Reproducible retrieval-only coding-memory benchmark, with no model in CI.

Gold labels are evaluator-only. The default calls the actual public observation
retriever over SQLite. An optional caller-supplied retriever receives only the
repository, scope and query, not this corpus or its relevance labels. Accuracy
here is synthetic retrieval accuracy, never generation or product accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from djobs.observations import search_observations
from djobs.storage.memory import memory_repository
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace

CORPUS = Path(__file__).resolve().parents[1] / "tests/fixtures/memory/retrieval_v1.json"
Retriever = Callable[[Any, Workspace, str, int], list[dict[str, Any]]]


def retrieval_metrics(selected: list[str], relevant: set[str], k: int) -> dict[str, float]:
    """Use fixed-k precision; absent slots are not counted as correct predictions."""
    unique = list(dict.fromkeys(selected))[:k]
    hits = [index for index, value in enumerate(unique, 1) if value in relevant]
    dcg = sum(1 / math.log2(index + 1) for index in hits)
    ideal = sum(1 / math.log2(index + 1) for index in range(1, min(k, len(relevant)) + 1))
    return {
        f"recall_at_{k}": len(hits) / len(relevant) if relevant else 0.0,
        f"precision_at_{k}": len(hits) / k,
        f"ndcg_at_{k}": dcg / ideal if ideal else 0.0,
        f"mrr_at_{k}": 1 / hits[0] if hits else 0.0,
    }


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile: well defined for small reproducible samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def load_corpus(path: Path = CORPUS) -> dict[str, Any]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported benchmark corpus schema")
    ids = [item["id"] for item in [*corpus["topics"], *corpus["extra_records"]]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate corpus record identity")
    for case in corpus["extra_queries"]:
        if set(case["relevant"]) - set(ids) or set(case["forbidden"]) - set(ids):
            raise ValueError("benchmark label references an unknown record")
    return corpus


def seed_corpus(
    repository: SQLiteJobRepository, workspace: Workspace, corpus: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, str], list[float]]:
    adapter = memory_repository(repository)
    aliases: dict[str, str] = {}
    durations = []
    records = [*corpus["topics"], *corpus["extra_records"]]
    records.extend(
        {
            "id": f"noise-{index:03d}",
            "summary": (
                f"Routine formatting pass {index}; adjusted comments and documentation layout."
            ),
            "event": "tool_result",
        }
        for index in range(48)
    )
    for index, item in enumerate(records):
        metadata = {
            "memory_status": "active",
            "stored_as_data": True,
            "repo_family_id": workspace.repo_family_id,
            "checkout_id": workspace.checkout_id,
            **item.get("metadata", {}),
        }
        family = "family:other" if item.get("family") == "other" else workspace.repo_family_id
        if family != workspace.repo_family_id:
            metadata["repo_family_id"] = family
        row = {
            "id": item["id"],
            "correlation_id": family,
            "agent_type": "benchmark",
            "session_id_hash": "benchmark-session",
            "event_type": item["event"],
            "tool_name": "fixture",
            "summary": item["summary"],
            "metadata_json": "{broken" if item.get("corrupt_metadata") else json.dumps(metadata),
            "created_at": (
                datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)
            ).isoformat(),
        }
        started = time.perf_counter()
        adapter.insert_observation(
            row, marker_event="context_injected", max_observations=1000, max_markers=256
        )
        durations.append((time.perf_counter() - started) * 1000)
        aliases[item["id"]] = item.get("duplicate_of", item["id"])
    cases: list[dict[str, Any]] = []
    for topic in corpus["topics"]:
        for group, query in topic["queries"].items():
            cases.append(
                {
                    "id": f"{topic['id']}:{group}",
                    "group": group,
                    "query": query,
                    "relevant": [topic["id"]],
                    "forbidden": [],
                }
            )
    cases.extend(corpus["extra_queries"])
    return cases, aliases, durations


def _default_retriever(repo: Any, workspace: Workspace, query: str, k: int):
    return search_observations(repo, workspace, query, limit=k)


def run(
    *,
    corpus_path: Path = CORPUS,
    repeats: int = 2,
    retriever: Retriever | None = None,
    prepare: Callable[[Any, Workspace], dict[str, Any]] | None = None,
    profile: str = "default-lexical",
) -> dict[str, Any]:
    corpus = load_corpus(corpus_path)
    repeats = max(1, min(int(repeats), 20))
    retrieve = retriever or _default_retriever
    with tempfile.TemporaryDirectory(prefix="djobs-memory-quality-") as temp:
        root = Path(temp)
        project = root / "project"
        project.mkdir()
        workspace = Workspace(
            root=str(project),
            workspace_id="repo:benchmark",
            checkout_id="repo:benchmark",
            repo_family_id="family:benchmark",
            correlation_ids=("repo:benchmark",),
            memory_correlation_ids=("family:benchmark", "repo:benchmark"),
            source="fixture",
        )
        database = root / "memory.db"
        repository = SQLiteJobRepository.from_path(database)
        try:
            cases, aliases, ingest_ms = seed_corpus(repository, workspace, corpus)
            preparation = prepare(repository, workspace) if prepare else {"provider_calls": 0}
            results = []
            durations = []
            payloads = []
            replay_ok = True
            for case in cases:
                original = None
                outputs = []
                for _ in range(repeats):
                    started = time.perf_counter()
                    selected = retrieve(repository, workspace, case["query"], 5)
                    durations.append((time.perf_counter() - started) * 1000)
                    encoded = json.dumps(selected, ensure_ascii=False, sort_keys=True, default=str)
                    payloads.append(math.ceil(len(encoded) / 4))
                    if original is None:
                        original = encoded
                    else:
                        replay_ok = replay_ok and encoded == original
                    outputs = selected
                ids = [aliases.get(str(item["id"]), str(item["id"])) for item in outputs]
                gold = set(case["relevant"])
                metrics = {}
                for k in (1, 3, 5):
                    metrics.update(retrieval_metrics(ids, gold, k))
                forbidden = set(case["forbidden"])
                globally_unsafe = {
                    "old-persistence",
                    "stale-oauth",
                    "contradiction-a",
                    "contradiction-b",
                    "quarantined",
                    "proposed",
                    "other-family",
                    "other-checkout",
                    "corrupt",
                }
                results.append(
                    {
                        "id": case["id"],
                        "group": case["group"],
                        "query": case["query"],
                        "relevant_ids": sorted(gold),
                        "selected_ids": ids,
                        "metrics": metrics,
                        "unsafe_selected": sorted(set(ids) & (globally_unsafe | forbidden)),
                        "irrelevant_selected": len([value for value in ids if value not in gold]),
                        "answerable": bool(gold),
                    }
                )
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for case in results:
                if case["answerable"]:
                    groups[case["group"]].append(case)
                    groups["all_answerable"].append(case)
                    if case["group"] in {"paraphrase", "zh", "mixed"}:
                        groups["multilingual_paraphrase"].append(case)
            aggregates = {}
            for name, items in groups.items():
                aggregates[name] = {
                    key: statistics.mean(item["metrics"][key] for item in items)
                    for key in items[0]["metrics"]
                }
                aggregates[name]["cases"] = len(items)
            total_selected = sum(len(case["selected_ids"]) for case in results)
            unsafe = sum(len(case["unsafe_selected"]) for case in results)
            irrelevant = sum(case["irrelevant_selected"] for case in results)
            total_size = sum(
                file.stat().st_size for file in root.glob("memory.db*") if file.is_file()
            )
            return {
                "schema_version": 1,
                "benchmark": "djobs-coding-memory-retrieval-v1",
                "profile": profile,
                "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
                "disclaimer": corpus["description"],
                "phases": {
                    "ingestion": "measured",
                    "retrieval": "measured",
                    "generation": "not_run",
                    "answer_judge": "not_run",
                },
                "cases": results,
                "aggregate": aggregates,
                "repeats": repeats,
                "deterministic_replay": replay_ok,
                "unsafe_injection_rate": unsafe / max(1, total_selected),
                "irrelevant_context_rate": irrelevant / max(1, total_selected),
                "negative_query_false_positive_rate": statistics.mean(
                    bool(case["selected_ids"]) for case in results if not case["answerable"]
                ),
                "retrieval_p50_ms": percentile(durations, 0.5),
                "retrieval_p95_ms": percentile(durations, 0.95),
                "ingestion_p50_ms": percentile(ingest_ms, 0.5),
                "ingestion_p95_ms": percentile(ingest_ms, 0.95),
                "db_and_sidecars_bytes": total_size,
                "selected_payload_tokens_p50": percentile(payloads, 0.5),
                "selected_payload_tokens_p95": percentile(payloads, 0.95),
                "token_measure": "ceil(unescaped JSON characters / 4); not provider billing",
                "preparation": preparation,
                "query_count": len(results),
                "retriever_invocations": len(results) * repeats,
                "external_network_calls": 0 if retriever is None else "profile_must_measure",
                "generation_model_calls": 0,
            }
        finally:
            repository.close()


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if baseline["corpus_sha256"] != candidate["corpus_sha256"]:
        raise ValueError("cannot compare different benchmark corpora")
    old = baseline["aggregate"]["multilingual_paraphrase"]
    new = candidate["aggregate"]["multilingual_paraphrase"]
    recall_delta = new["recall_at_5"] - old["recall_at_5"]
    precision_delta = new["precision_at_5"] - old["precision_at_5"]
    gates = {
        "exact_recall_no_regression": candidate["aggregate"]["exact"]["recall_at_5"]
        >= baseline["aggregate"]["exact"]["recall_at_5"],
        "multilingual_recall_gain": recall_delta >= 0.15 - 1e-9,
        "precision_preserved": precision_delta >= -0.02 - 1e-9,
        "no_unsafe_injection": candidate["unsafe_injection_rate"] == 0,
        "deterministic_replay": candidate["deterministic_replay"],
    }
    return {
        "gates": gates,
        "quality_gate_pass": all(gates.values()),
        "recall_at_5_absolute_delta": recall_delta,
        "precision_at_5_absolute_delta": precision_delta,
        "scope": (
            "retrieval quality only; latency, ownership, privacy "
            "and real-model evidence are separate gates"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    result = run(corpus_path=args.corpus, repeats=args.repeats)
    if args.baseline:
        result["comparison"] = compare(
            json.loads(args.baseline.read_text(encoding="utf-8-sig")), result
        )
    encoded = json.dumps(result, ensure_ascii=True, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
        print(
            json.dumps(
                {key: value for key, value in result.items() if key != "cases"},
                ensure_ascii=True,
                indent=2,
            )
        )
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
