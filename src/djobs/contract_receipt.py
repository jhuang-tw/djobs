"""Cross-field consistency verification for advisory host-contract receipts.

The receipt hash provides deterministic integrity checking, not cryptographic
provenance.  This module additionally verifies that the embedded receipt agrees
with the response fields it claims to bind.
"""

from __future__ import annotations

from typing import Any

from djobs.host_contract import SCHEMA_MAJOR, _hash, envelope


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _counts_are_consistent(
    response: dict[str, Any],
    receipt: dict[str, Any],
    budget: dict[str, Any],
) -> bool:
    candidate_count = _integer(receipt.get("candidate_count"))
    selected_count = _integer(receipt.get("selected_count"))
    rejected_count = _integer(receipt.get("rejected_count"))
    budget_candidates = _integer(budget.get("candidate_count"))
    budget_selected = _integer(budget.get("selected_count"))
    observations = response.get("repository_observations")
    if candidate_count is None or selected_count is None or rejected_count is None:
        return False
    if not isinstance(observations, list):
        return False
    if candidate_count < 0 or selected_count < 0 or rejected_count < 0:
        return False
    if selected_count > candidate_count:
        return False
    if rejected_count != candidate_count - selected_count:
        return False
    if selected_count != len(observations):
        return False
    if budget_candidates != candidate_count or budget_selected != selected_count:
        return False
    reasons = _mapping(receipt.get("rejection_reasons"))
    max_items = _integer(reasons.get("max_items"))
    token_budget = _integer(reasons.get("token_budget"))
    return (
        max_items is not None
        and token_budget is not None
        and max_items >= 0
        and token_budget >= 0
        and max_items + token_budget == rejected_count
    )


def verify_receipt_payload(response: Any) -> dict[str, Any]:
    """Verify receipt integrity and internal consistency without storing state.

    A valid result means the response and receipt are self-consistent.  It does
    not authenticate the producer because the digest is not a digital signature.
    """

    result = envelope("receipt")
    if not isinstance(response, dict):
        result.update(
            {
                "ok": False,
                "continue_workflow": True,
                "valid": False,
                "verification_kind": "receipt_consistency",
                "assurance": "integrity_and_internal_consistency_not_authenticity",
                "checks": {},
                "failed_checks": ["response_object"],
                "error": {
                    "code": "invalid_receipt_input",
                    "message": "response must be a JSON object",
                },
            }
        )
        return result

    receipt = _mapping(response.get("receipt"))
    if not receipt:
        result.update(
            {
                "ok": False,
                "continue_workflow": True,
                "valid": False,
                "verification_kind": "receipt_consistency",
                "assurance": "integrity_and_internal_consistency_not_authenticity",
                "checks": {},
                "failed_checks": ["receipt_present"],
                "error": {
                    "code": "missing_receipt",
                    "message": "response has no receipt",
                },
            }
        )
        return result

    provider = _mapping(response.get("provider"))
    repository = _mapping(response.get("repository"))
    budget = _mapping(response.get("budget"))
    schema = _mapping(response.get("schema"))
    filters = _mapping(response.get("requested_filters"))
    expected_hash = str(receipt.get("output_hash") or "")
    actual_hash = _hash({key: value for key, value in response.items() if key != "receipt"})
    candidate_count = _integer(receipt.get("candidate_count"))
    selected_count = _integer(receipt.get("selected_count"))
    truncated = (
        candidate_count is not None
        and selected_count is not None
        and selected_count < candidate_count
    )

    checks = {
        "output_hash": bool(expected_hash) and expected_hash == actual_hash,
        "hash_scope": receipt.get("hash_scope") == "response_without_receipt",
        "receipt_kind": receipt.get("receipt_kind") == "observation_query",
        "operation": response.get("operation") == "observation",
        "schema_major": schema.get("major") == SCHEMA_MAJOR,
        "advisory_boundary": (
            response.get("authority") == "advisory"
            and response.get("side_effects") is False
            and response.get("writes_database") is False
            and response.get("continue_workflow") is True
        ),
        "request_id": (
            isinstance(response.get("request_id"), str)
            and bool(response["request_id"])
            and receipt.get("request_id") == response["request_id"]
        ),
        "query_fingerprint": receipt.get("query_fingerprint") == _hash(filters),
        "provider_contract": (
            provider.get("contract") == "djobs.host-contract"
            and provider.get("operation") == "observation"
        ),
        "provider_version": receipt.get("provider_version") == provider.get("version"),
        "provider_build_commit": (
            receipt.get("provider_build_commit") == provider.get("build_commit")
        ),
        "repository_fingerprint": (
            isinstance(repository.get("fingerprint"), str)
            and bool(repository["fingerprint"])
            and receipt.get("repository_fingerprint") == repository["fingerprint"]
        ),
        "repository_head": (
            isinstance(repository.get("head"), str)
            and bool(repository["head"])
            and receipt.get("repository_head") == repository["head"]
        ),
        "token_budget": receipt.get("token_budget") == budget,
        "counts": _counts_are_consistent(response, receipt, budget),
        "truncation": (
            isinstance(receipt.get("truncated"), bool)
            and isinstance(budget.get("truncated"), bool)
            and receipt["truncated"] == budget["truncated"] == truncated
        ),
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    result.update(
        {
            "ok": True,
            "continue_workflow": True,
            "valid": not failed_checks,
            "verification_kind": "receipt_consistency",
            "assurance": "integrity_and_internal_consistency_not_authenticity",
            "request_id": receipt.get("request_id"),
            "expected_output_hash": expected_hash,
            "actual_output_hash": actual_hash,
            "checks": checks,
            "failed_checks": failed_checks,
            "receipt": receipt,
        }
    )
    return result
