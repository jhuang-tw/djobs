"""Explicit quarantined session CLI. Export writes JSON to stdout only.

A shell redirect is controlled by the user. djobs does not create, overwrite or
install a foreign harness session, credential/config file, or active prompt.
"""

from __future__ import annotations

import argparse
import json
import os

from djobs.artifact_cli import terminal_review_gate
from djobs.memory import memory_action


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="djobs session")
    sub = parser.add_subparsers(dest="operation", required=True)
    discover = sub.add_parser(
        "discover", help="List bounded files under an explicit root without reading content"
    )
    discover.add_argument("root")
    for operation in ("preview", "import"):
        command = sub.add_parser(operation)
        command.add_argument("root")
        command.add_argument("path", help="Relative file under the explicitly selected root")
        command.add_argument(
            "--harness", choices=["claude", "codex", "opencode", "djobs"], required=True
        )
        command.add_argument("--id", action="append", dest="selected_ids")
        if operation == "import":
            command.add_argument("--expected-hash")
            command.add_argument("--repo-family")
            command.add_argument("--yes", action="store_true")
            command.add_argument("--dry-run", action="store_true")
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("memory_id")
    show.add_argument("--depth", type=int, choices=[0, 1, 2], default=2)
    review = sub.add_parser("review")
    review.add_argument("memory_id")
    review.add_argument("--apply", action="store_true")
    export = sub.add_parser(
        "export", help="Write a redacted portable conversation bundle to stdout"
    )
    export.add_argument("memory_id")
    args = parser.parse_args(argv)
    try:
        if args.operation == "export":
            from djobs.session_memory import SessionMemory
            from djobs.storage.read_only import connect_read_only
            from djobs.storage.sqlite import SQLiteJobRepository
            from djobs.workspace import resolve_workspace, shared_db_path

            connection = connect_read_only(shared_db_path())
            if connection is None:
                raise ValueError("memory store is not initialized")
            repo = SQLiteJobRepository(connection)
            try:
                result = SessionMemory(repo, resolve_workspace(cwd=os.getcwd())).export(
                    args.memory_id
                )
            finally:
                repo.close()
            print(json.dumps(result["document"], ensure_ascii=True, indent=2))
            return 0
        document = {"operation": "get" if args.operation == "show" else args.operation}
        for field in ("root", "path", "harness", "selected_ids", "depth"):
            value = getattr(args, field, None)
            if value is not None:
                document[field] = value
        confirm = bool(getattr(args, "yes", False)) and not getattr(args, "dry_run", False)
        if args.operation == "import" and confirm:
            if not args.selected_ids or not args.expected_hash or not args.repo_family:
                parser.error(
                    "import --yes requires selected --id, --expected-hash and --repo-family"
                )
            document["expected_hash"] = args.expected_hash
            document["expected_family"] = args.repo_family
        result = json.loads(
            memory_action(
                "session",
                document=document,
                memory_id=getattr(args, "memory_id", None),
                confirm=confirm,
                review_gate=terminal_review_gate() if getattr(args, "apply", False) else None,
                cwd=os.getcwd(),
                token_budget=4000,
            )
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("ok") else 1
    except Exception:
        print(
            json.dumps(
                {"ok": False, "error": "session_operation_unavailable", "continue_coding": True}
            )
        )
        return 1
