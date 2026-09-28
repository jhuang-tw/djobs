# Advisory host contract

The host contract is a separate, versioned surface for external workflow systems that need
repository evidence without delegating workflow authority to djobs.

## Commands

```text
djobs contract --schema-major 1 capabilities
djobs contract --schema-major 1 observation --repository-head <sha> ...
djobs contract --schema-major 1 receipt --response-file response.json
```

New integrations should use the `djobs contract ...` subcommand so the human-facing contract tools
stay under the canonical `djobs` CLI. The standalone `djobs-contract` and
`djobs-contract-mcp` executables remain available for compatibility with existing integrations.

## Authority boundary

The advisory surface exposes only `capabilities`, `observation`, and receipt verification. It
never registers an agent, recovers a lease, captures a Git/workflow observation, creates or claims a task,
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


## Strict SQLite reads

Advisory reads do not change the source DB bytes/mtime or create source WAL/SHM files. To avoid
SQLite read-only WAL side effects, the implementation captures a bounded private temporary DB/WAL
view, verifies source generations and hashes, and reads that view in query-only mode. It removes
the temporary copy on close/failure. This is temporary filesystem I/O, not another persistent memory
authority. The source is never declared immutable and live WAL content is retained.

Capture limits are 64 MiB and a 0.5-second copy/verification deadline. Source churn, over-limit files,
and errors return the existing fail-open contract instead of reporting a false empty database.
Receipt integrity still describes the returned capture and requested repository identity; it does
not assert that the live source stayed unchanged after capture. No query triggers lazy indexing.
