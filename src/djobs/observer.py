"""Agent-agnostic Git sidecar for clients without lifecycle hooks."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from djobs.handoff import _resolve
from djobs.observations import capture_repository_snapshot


def observe_once(root: str | Path, *, client: str = "filesystem-sidecar") -> bool:
    workspace, agent, _queue, repo = _resolve(
        roots=None,
        cwd=str(Path(root).expanduser()),
        agent_type=client,
        session_id=f"observer:{Path(root).expanduser()}",
    )
    return capture_repository_snapshot(repo, workspace, agent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="djobs observe")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--client", default="filesystem-sidecar")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args(argv)
    if not args.watch:
        observe_once(args.root, client=args.client)
        return 0
    interval = max(1.0, min(args.interval, 60.0))
    try:
        while True:
            observe_once(args.root, client=args.client)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
