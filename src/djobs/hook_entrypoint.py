"""Host-neutral command entrypoint for Codex and Claude Code lifecycle hooks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable


def _payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("hook input must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m djobs.hook_entrypoint")
    parser.add_argument(
        "event",
        choices=["session-start", "user-prompt", "pre", "post", "stop"],
    )
    parser.add_argument("--host", choices=["codex", "claude"], required=True)
    parser.add_argument("--db")
    parser.add_argument("--mode", choices=["off", "smart", "all"], default="smart")
    args = parser.parse_args(argv)

    if args.db:
        os.environ["DJOBS_DB"] = os.path.abspath(os.path.expanduser(args.db))
    os.environ["DJOBS_AGENT_TYPE"] = args.host

    try:
        data = _payload()
        from djobs import lifecycle

        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "session-start": lifecycle.session_start,
            "user-prompt": lifecycle.user_prompt_submit,
            "pre": lifecycle.pre_tool_use,
            "post": lifecycle.post_tool_use,
            "stop": lifecycle.stop,
        }
        result = handlers[args.event](data, agent_type=args.host)
    except Exception:
        result = {}

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
