"""CLI for storage integrity, backup, and bounded compaction."""

from __future__ import annotations

import argparse
import json
import os


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="djobs storage",
        description="Inspect and maintain the local djobs storage safely.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    check_parser = subparsers.add_parser("check", help="Run a storage integrity check")
    check_parser.add_argument("--json", action="store_true", dest="as_json")
    backup_parser = subparsers.add_parser("backup", help="Create a consistent SQLite backup")
    backup_parser.add_argument("destination", nargs="?")
    backup_parser.add_argument("--json", action="store_true", dest="as_json")
    compact_parser = subparsers.add_parser(
        "compact", help="Preview or apply bounded passive-memory compaction"
    )
    compact_parser.add_argument("--dry-run", action="store_true")
    compact_parser.add_argument("--keep-recent", type=int, default=100)
    compact_parser.add_argument("--yes", action="store_true")
    compact_parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.action == "compact":
        from djobs.memory import memory_action

        payload = json.loads(
            memory_action(
                "compact",
                cwd=os.getcwd(),
                agent_type="cli",
                dry_run=bool(args.dry_run),
                keep_recent=int(args.keep_recent),
                confirm=bool(args.yes),
            )
        )
    else:
        from djobs.storage.maintenance import storage_maintenance
        from djobs.storage.sqlite import SQLiteJobRepository
        from djobs.workspace import shared_db_path

        repo = SQLiteJobRepository.from_path(shared_db_path())
        try:
            maintenance = storage_maintenance(repo)
            payload = (
                maintenance.integrity_check()
                if args.action == "check"
                else maintenance.backup(args.destination)
            )
        finally:
            repo.close()

    if bool(getattr(args, "as_json", False)):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif args.action == "check":
        mark = "OK" if payload.get("ok") else "FAIL"
        print(
            f"[{mark}] {payload.get('backend')} storage: " + "; ".join(payload.get("messages", []))
        )
    elif args.action == "backup":
        if payload.get("created"):
            print(f"Backup created: {payload['backup_path']}")
        else:
            print(f"Backup not created: {payload.get('reason', 'unknown reason')}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload.get("ok") else 1
