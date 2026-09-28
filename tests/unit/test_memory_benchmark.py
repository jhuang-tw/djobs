from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from scripts.benchmark_memory import compare, load_corpus, percentile, retrieval_metrics, run


def test_metrics_have_fixed_k_denominator_and_discount_late_hits() -> None:
    metrics = retrieval_metrics(["noise", "hit", "hit"], {"hit"}, 5)
    assert metrics["recall_at_5"] == 1
    assert metrics["precision_at_5"] == 0.2
    assert metrics["mrr_at_5"] == 0.5
    assert metrics["ndcg_at_5"] == pytest.approx(0.6309297535714575)
    assert retrieval_metrics([], {"hit"}, 3)["recall_at_3"] == 0
    assert percentile([1, 2, 3, 100], 0.95) == 100


def test_corpus_has_multilingual_cases_and_no_unknown_gold_ids() -> None:
    corpus = load_corpus()
    assert len(corpus["topics"]) >= 12
    for topic in corpus["topics"]:
        assert set(topic["queries"]) == {"exact", "paraphrase", "zh", "mixed"}
    assert {case["group"] for case in corpus["extra_queries"]} >= {
        "temporal",
        "quarantine",
        "unsupported",
        "checkout",
        "contradiction",
        "irrelevant",
        "corruption",
        "injection",
    }


def test_benchmark_runs_real_sqlite_retrieval_without_generation_or_network(monkeypatch) -> None:
    import socket

    def forbidden_network(*args, **kwargs):
        raise AssertionError("default benchmark must not use external services")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    result = run(repeats=2)
    assert result["query_count"] == 56
    assert result["aggregate"]["exact"]["recall_at_5"] == 1
    assert result["deterministic_replay"] is True
    assert result["generation_model_calls"] == 0
    assert result["external_network_calls"] == 0
    assert result["phases"]["generation"] == "not_run"
    assert result["db_and_sidecars_bytes"] > 0
    assert result["retrieval_p95_ms"] >= result["retrieval_p50_ms"]
    assert result["aggregate"]["multilingual_paraphrase"]["cases"] == 36


def test_comparison_rejects_mismatched_datasets_and_does_not_claim_stub_success() -> None:
    baseline = {
        "corpus_sha256": "one",
        "aggregate": {
            "exact": {"recall_at_5": 1},
            "multilingual_paraphrase": {
                "recall_at_5": 0.5,
                "precision_at_5": 0.1,
            },
        },
        "unsafe_injection_rate": 0,
        "deterministic_replay": True,
    }
    candidate = copy.deepcopy(baseline)
    assert compare(baseline, candidate)["quality_gate_pass"] is False
    candidate["aggregate"]["multilingual_paraphrase"]["recall_at_5"] = 0.7
    assert compare(baseline, candidate)["quality_gate_pass"] is True
    candidate["unsafe_injection_rate"] = 0.01
    assert compare(baseline, candidate)["quality_gate_pass"] is False
    candidate["corpus_sha256"] = "two"
    with pytest.raises(ValueError, match="different benchmark corpora"):
        compare(baseline, candidate)


def test_future_corpus_schema_is_not_silently_loaded(tmp_path: Path) -> None:
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        load_corpus(path)
