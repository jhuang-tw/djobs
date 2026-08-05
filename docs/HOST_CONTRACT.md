# Advisory host contract

The host contract is a separate, versioned surface for external workflow systems that need
repository evidence without delegating workflow authority to djobs.

## Commands

```text
djobs contract --schema-major 1 capabilities
djobs contract --schema-major 1 observation --repository-head <sha> ...
djobs contract --schema-major 1 receipt --response-file response.json
```

The equivalent standalone executables are `djobs-contract` and `djobs-contract-mcp`.

## Authority boundary

The advisory surface exposes only `capabilities`, `observation`, and receipt verification. It
never registers an agent, recovers a lease, captures a snapshot, creates or claims a task,
changes task status, schedules work, creates a worktree, or writes the djobs database.

`checkpoint` and `handoff` remain available in the established coding surface for djobs-native
coordination, but capabilities mark them as side-effecting and unavailable in advisory mode.
External workflow hosts must not expose or call them through the advisory integration.

## Fail-open rule

Contract failures return JSON with `ok=false` and `continue_workflow=true`. The external host
must record the provider failure, treat djobs evidence as empty, and continue its own canonical
workflow. A djobs response must never create or clear an external blocker or complete an
external task.

## Identity and freshness

Production consumers should always send the exact repository fingerprint and HEAD bound by
their own workflow state. A mismatch rejects the response. Observation filters execute in the
SQLite query, not by scanning and then interpreting free text. Legacy rows without a bound HEAD
are returned only when the caller does not require `--repository-head`; consumers should reject
`identity_confidence=legacy_unbound` or `repository_bound` for acceptance evidence.

## Compatibility

Schema major 1 is additive within the major version. Consumers must ignore unknown fields.
Required fields are not removed or retyped within major 1. A new major requires explicit
consumer opt-in.

## Receipt semantics

The embedded receipt deterministically binds the response body to the requested filters,
repository identity, HEAD, provider build, budget, counts, and truncation state. The
`djobs contract ... receipt` command and advisory MCP `receipt` tool verify both the output
digest and cross-field consistency. Verification returns per-field `checks` and
`failed_checks`, allowing hosts to record the exact rejection reason.

The SHA-256 digest is an integrity checksum, not a signature or proof of authentic producer
identity. A valid result means that the response and receipt are internally consistent. It does
not prove that an external host consumed or accepted the evidence. The host must keep its own
audit record of accepted and rejected observation IDs and must fail open when verification is
unavailable.
