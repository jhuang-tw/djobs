from __future__ import annotations

from typing import Any

from djobs import host_contract as contract
from djobs.contract_receipt import verify_receipt_payload


def _response() -> dict[str, Any]:
    filters = {
        "query": "parser",
        "task_id": "task-a",
        "feature_id": "feature-a",
        "repository_head": "a" * 40,
        "repository_fingerprint": "family:test-repository",
        "kind": None,
        "status": "active",
        "since": None,
        "max_age_seconds": None,
        "correlation_id": None,
        "session_id_hash": None,
        "max_items": 12,
        "token_budget": 800,
    }
    budget = {
        "requested_tokens": 800,
        "tokens_before_truncation": 240,
        "estimated_selected_tokens": 260,
        "candidate_count": 0,
        "selected_count": 0,
        "dropped_for_budget_count": 0,
        "truncated": False,
        "ranking_method": contract.RANKING_METHOD,
        "critical_evidence_omitted": False,
    }
    response = contract.envelope(
        "observation",
        generated_at="2026-08-05T12:00:00+00:00",
    )
    response.update(
        {
            "ok": True,
            "continue_workflow": True,
            "mode": "advisory",
            "request_id": "request-a",
            "state": "empty",
            "repository": {
                "name": "repo",
                "fingerprint": "family:test-repository",
                "head": "a" * 40,
                "branch": "main",
                "dirty": False,
                "checkout_id": "repo:test-checkout",
                "workspace_fingerprint": "sha256:" + "b" * 64,
                "identity_confidence": "exact",
            },
            "requested_filters": filters,
            "filter_execution": "sqlite_query_stage",
            "repository_observations": [],
            "budget": budget,
        }
    )
    receipt = {
        "receipt_kind": "observation_query",
        "request_id": response["request_id"],
        "query_fingerprint": contract._hash(filters),
        "requested_at": response["generated_at"],
        "completed_at": "2026-08-05T12:00:01+00:00",
        "candidate_count": 0,
        "selected_count": 0,
        "rejected_count": 0,
        "rejection_reasons": {"max_items": 0, "token_budget": 0},
        "output_hash": contract._hash(response),
        "hash_scope": "response_without_receipt",
        "provider_version": response["provider"]["version"],
        "provider_build_commit": response["provider"]["build_commit"],
        "repository_fingerprint": response["repository"]["fingerprint"],
        "repository_head": response["repository"]["head"],
        "token_budget": budget,
        "truncated": False,
    }
    response["receipt"] = receipt
    return response


def _refresh_output_hash(response: dict[str, Any]) -> None:
    body = {key: value for key, value in response.items() if key != "receipt"}
    response["receipt"]["output_hash"] = contract._hash(body)


def test_receipt_consistency_verifier_accepts_complete_response() -> None:
    result = verify_receipt_payload(_response())

    assert result["ok"] is True
    assert result["valid"] is True
    assert result["failed_checks"] == []
    assert all(result["checks"].values())
    assert result["verification_kind"] == "receipt_consistency"
    assert result["assurance"] == "integrity_and_internal_consistency_not_authenticity"


def test_receipt_only_repository_tampering_is_detected_beyond_output_hash() -> None:
    response = _response()
    response["receipt"]["repository_head"] = "c" * 40

    result = verify_receipt_payload(response)

    assert result["checks"]["output_hash"] is True
    assert result["checks"]["repository_head"] is False
    assert result["failed_checks"] == ["repository_head"]
    assert result["valid"] is False


def test_rehashed_body_with_mismatched_request_id_is_rejected() -> None:
    response = _response()
    response["request_id"] = "request-b"
    _refresh_output_hash(response)

    result = verify_receipt_payload(response)

    assert result["checks"]["output_hash"] is True
    assert result["checks"]["request_id"] is False
    assert result["valid"] is False


def test_rehashed_filters_with_stale_query_fingerprint_are_rejected() -> None:
    response = _response()
    response["requested_filters"]["query"] = "different query"
    _refresh_output_hash(response)

    result = verify_receipt_payload(response)

    assert result["checks"]["output_hash"] is True
    assert result["checks"]["query_fingerprint"] is False
    assert result["valid"] is False


def test_inconsistent_counts_are_rejected() -> None:
    response = _response()
    response["receipt"]["selected_count"] = 1

    result = verify_receipt_payload(response)

    assert result["checks"]["output_hash"] is True
    assert result["checks"]["counts"] is False
    assert result["valid"] is False


def test_missing_receipt_and_non_object_input_fail_open() -> None:
    missing = verify_receipt_payload({"operation": "observation"})
    invalid = verify_receipt_payload(["not", "an", "object"])

    assert missing["ok"] is False
    assert missing["continue_workflow"] is True
    assert missing["failed_checks"] == ["receipt_present"]
    assert invalid["ok"] is False
    assert invalid["continue_workflow"] is True
    assert invalid["failed_checks"] == ["response_object"]


def test_compatibility_wrapper_uses_hardened_verifier() -> None:
    response = _response()
    response["receipt"]["provider_version"] = "0.0.0"

    result = contract.verify_receipt_payload(response)

    assert result["checks"]["output_hash"] is True
    assert result["checks"]["provider_version"] is False
    assert result["valid"] is False
