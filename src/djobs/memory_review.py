"""Explicit trusted-product review; no JSON role or confirm flag grants acceptance.

An application may inject its native human-confirmation callback. Agent-facing
MCP does not accept this object. This is an API authority boundary, not a sandbox
against arbitrary local Python code with the user's database access.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from djobs.memory_artifacts import ArtifactError, safe_text

Decision = Literal["accept", "reject"]


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    operation: str
    artifact_id: str
    binding_hash: str
    preview_json: str


@dataclass(slots=True)
class _Approval:
    issuer: object
    binding_hash: str
    decision: Decision
    used: bool = False


class ReviewGate:
    """The trusted host must bind the callback to a real explicit human action."""

    def __init__(
        self,
        authorizer: Callable[[ReviewRequest], Decision | None],
        *,
        reviewer: str,
        policy: str = "explicit-product-review-v1",
    ) -> None:
        if not callable(authorizer):
            raise ArtifactError("review_authorizer_required")
        self._authorizer = authorizer
        self.reviewer = safe_text(reviewer, 160, required=True)
        self.policy = safe_text(policy, 160, required=True)
        self._issuer = object()

    def request(self, request: ReviewRequest) -> _Approval:
        decision = self._authorizer(request)
        if decision not in {"accept", "reject"}:
            raise ArtifactError("review_not_authorized")
        return _Approval(self._issuer, request.binding_hash, decision)

    def consume(self, approval: _Approval, binding_hash: str) -> Decision:
        if (
            not isinstance(approval, _Approval)
            or approval.issuer is not self._issuer
            or approval.used
            or approval.binding_hash != binding_hash
        ):
            raise ArtifactError("stale_or_invalid_review")
        approval.used = True
        return approval.decision
