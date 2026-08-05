"""CLI for the versioned advisory host contract."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from djobs.host_contract import (
    SCHEMA_MAJOR,
    STATUSES,
    ObservationRequest,
    build_observation_response,
    capabilities_payload,
    dumps,
    error_payload,
    verify_receipt_payload,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="djobs contract",
        description="Versioned advisory-only repository evidence contract.",
    )
    parser.add_argument("--schema-major", type=int, default=SCHEMA_MAJOR)
    parser.add_argument("--mode", choices=["advisory"], default="advisory")
    parser.add_argument("--pretty", action="store_true")
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("capabilities")
    observation = commands.add_parser("observation")
    for flag in ("query", "task-id", "feature-id", "repository-head", "repository-fingerprint"):
        observation.add_argument(f"--{flag}")
    observation.add_argument("--kind")
    observation.add_argument("--status", default="active", choices=STATUSES)
    observation.add_argument("--since")
    observation.add_argument("--max-age-seconds", type=int)
    observation.add_argument("--correlation-id")
    observation.add_argument("--session-id")
    observation.add_argument("--max-items", type=int, default=12)
    observation.add_argument("--token-budget", type=int, default=800)
    observation.add_argument("--request-id")
    observation.add_argument("--cwd")
    observation.add_argument("--db", type=Path)
    receipt = commands.add_parser("receipt")
    source = receipt.add_mutually_exclusive_group()
    source.add_argument("--response-file", type=Path)
    source.add_argument("--response-json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.schema_major != SCHEMA_MAJOR:
        payload = error_payload(
            "unsupported_schema_major",
            f"supported schema major is {SCHEMA_MAJOR}",
            str(args.operation),
        )
    elif args.operation == "capabilities":
        payload = capabilities_payload()
    elif args.operation == "observation":
        payload = build_observation_response(
            ObservationRequest(
                query=args.query,
                task_id=args.task_id,
                feature_id=args.feature_id,
                repository_head=args.repository_head,
                repository_fingerprint=args.repository_fingerprint,
                kind=args.kind,
                status=args.status,
                since=args.since,
                max_age_seconds=args.max_age_seconds,
                correlation_id=args.correlation_id,
                session_id=args.session_id,
                max_items=args.max_items,
                token_budget=args.token_budget,
                request_id=args.request_id,
            ),
            cwd=args.cwd,
            db_path=args.db,
        )
    else:
        try:
            raw = (
                args.response_file.read_text(encoding="utf-8")
                if args.response_file
                else args.response_json
                if args.response_json
                else sys.stdin.read()
            )
            payload = verify_receipt_payload(json.loads(raw))
        except (OSError, json.JSONDecodeError) as exc:
            payload = error_payload("invalid_receipt_input", str(exc), "receipt")
    sys.stdout.write(dumps(payload, pretty=args.pretty) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
