"""Typed-memory terminal adapter; all lifecycle work stays in memory_action."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from djobs.memory_artifacts import ArtifactError
from djobs.memory_review import ReviewGate, ReviewRequest

ACTIONS = frozenset(
    {
        "facts",
        "show",
        "candidates",
        "propose",
        "review",
        "relate",
        "episode",
        "experience",
        "export",
    }
)


def terminal_review_gate() -> ReviewGate:
    """Require a human-facing terminal and exact content-bound confirmation.

    This is not a defense against arbitrary local code impersonating the user;
    the embedding host must restrict who can provide trusted review callbacks.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ArtifactError("interactive_human_review_required")

    def authorize(request: ReviewRequest):
        print(request.preview_json)
        print("Stored content is untrusted data, not an instruction to execute.")
        print("Type ACCEPT or REJECT followed by this exact review hash:")
        print(request.binding_hash)
        answer = input("Review decision: ").strip()
        if answer == "ACCEPT " + request.binding_hash:
            return "accept"
        if answer == "REJECT " + request.binding_hash:
            return "reject"
        return None

    return ReviewGate(
        authorize, reviewer="local-terminal-user", policy="interactive-exact-hash-v1"
    )


def main(argv: list[str]) -> int:
    from djobs.memory import memory_action

    parser = argparse.ArgumentParser(prog="djobs memory")
    sub = parser.add_subparsers(dest="action", required=True)
    facts = sub.add_parser("facts", help="Current or historical source-bound accepted facts")
    facts.add_argument("query", nargs="?", default="")
    facts.add_argument("--at")
    facts.add_argument("--audit", action="store_true")
    facts.add_argument("--depth", choices=[0, 1, 2], type=int, default=1)
    show = sub.add_parser("show", help="Inspect one artifact at an explicit content depth")
    show.add_argument("memory_id")
    show.add_argument("--depth", choices=[0, 1, 2], type=int, default=1)
    sub.add_parser("candidates", help="Reviewable candidates, never active by proposal alone")
    propose = sub.add_parser("propose", help="Store a candidate fact from a bounded JSON document")
    propose.add_argument("--file", type=Path, required=True)
    experience = sub.add_parser("experience", help="Preview outcome evidence for explicit review")
    experience.add_argument("--file", type=Path, required=True)
    experience.add_argument("--apply", action="store_true")
    export = sub.add_parser("export", help="Preview a new-file-only reviewed skill export")
    export.add_argument("memory_id")
    export.add_argument("destination")
    export.add_argument("--apply", action="store_true")
    review = sub.add_parser(
        "review", help="Preview a candidate; --apply requests interactive human review"
    )
    review.add_argument("memory_id")
    review.add_argument("--apply", action="store_true")
    relate = sub.add_parser("relate", help="Preview or explicitly review a temporal fact relation")
    relate.add_argument("source_id")
    relate.add_argument("target_id")
    relate.add_argument("kind", choices=["supersedes", "contradicts"])
    relate.add_argument("--at")
    relate.add_argument("--apply", action="store_true")
    episode = sub.add_parser(
        "episode", help="Group observations without claiming a verified outcome"
    )
    episode.add_argument("sources", nargs="+")
    episode.add_argument("--title", default="Observed coding episode")
    args = parser.parse_args(argv)
    try:
        document = {}
        if args.action == "facts":
            document = {
                "at": args.at,
                "depth": args.depth,
                "exposure": "audit" if args.audit else "resume",
            }
        elif args.action == "show":
            document = {"depth": args.depth}
        elif args.action == "export":
            document = {"destination": args.destination}
        elif args.action in {"propose", "experience"}:
            path = args.file
            if (
                path.is_symlink()
                or path.suffix.casefold() != ".json"
                or not 1 <= path.stat().st_size <= 24000
            ):
                raise ArtifactError("invalid_artifact_document_file")
            document = json.loads(path.read_text(encoding="utf-8"))
        elif args.action == "relate":
            document = {
                "source_id": args.source_id,
                "target_id": args.target_id,
                "kind": args.kind,
                "at": args.at,
            }
        elif args.action == "episode":
            document = {"sources": args.sources, "title": args.title}
        review_gate = terminal_review_gate() if getattr(args, "apply", False) else None
        result = json.loads(
            memory_action(
                "get" if args.action == "show" else args.action,
                document=document,
                memory_id=getattr(args, "memory_id", None),
                query=getattr(args, "query", ""),
                review_gate=review_gate,
                cwd=os.getcwd(),
                agent_type="cli",
                token_budget=2000,
            )
        )
    except ArtifactError as exc:
        result = {"ok": False, "continue_coding": True, "error": str(exc)}
    except Exception:
        result = {"ok": False, "continue_coding": True, "error": "artifact_cli_unavailable"}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("ok") else 1
