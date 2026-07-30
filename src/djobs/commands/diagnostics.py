"""CLI for bounded, redacted fail-open diagnostics."""

from __future__ import annotations

import argparse
import json
from typing import Any, cast


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="djobs diagnostics",
        description="Inspect bounded, redacted fail-open errors.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Confirm diagnostic deletion")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    from djobs.diagnostics import clear_diagnostics, list_diagnostics
    from djobs.storage.sqlite import SQLiteJobRepository
    from djobs.workspace import shared_db_path

    repo = SQLiteJobRepository.from_path(shared_db_path())
    payload: dict[str, Any]
    try:
        if args.clear:
            if not args.yes:
                payload = {
                    "ok": False,
                    "requires_confirmation": True,
                    "message": "Pass --yes to clear bounded diagnostics.",
                }
                print(json.dumps(payload, indent=2) if args.as_json else payload["message"])
                return 1
            payload = {"ok": True, "cleared": clear_diagnostics(repo)}
        else:
            items = list_diagnostics(repo, limit=args.limit)
            payload = {"ok": True, "count": len(items), "diagnostics": items}
    finally:
        repo.close()

    if args.as_json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    if args.clear:
        print(f"Cleared {payload['cleared']} diagnostic record(s).")
        return 0
    items = cast(list[dict[str, Any]], payload["diagnostics"])
    if not items:
        print("No fail-open diagnostics recorded.")
        return 0
    print(f"djobs diagnostics — {len(items)} record(s)\n")
    for item in items:
        print(
            f"- {item['component']} [{item['error_type']}] x{item['occurrence_count']}: "
            f"{item['last_message']}"
        )
        print(f"  last seen: {item['last_seen_at']}")
    return 0
