"""Client-neutral command entrypoint for coding-agent lifecycle adapters."""

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
        choices=[
            "session-start",
            "post",
            "post-failure",
            "pre-compact",
            "session-end",
            # Compatibility with hook files installed by the previous prerelease.
            "user-prompt",
            "pre",
            "stop",
        ],
    )
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--client", help="Arbitrary agent/client identifier")
    identity.add_argument("--host", help="Deprecated alias retained for old hook files")
    parser.add_argument("--db")
    parser.add_argument("--mode", choices=["off", "smart", "all"], default="smart")
    args = parser.parse_args(argv)

    client = str(args.client or args.host).strip().lower()
    if not client:
        parser.error("--client must not be empty")
    if args.db:
        os.environ["DJOBS_DB"] = os.path.abspath(os.path.expanduser(args.db))
    os.environ["DJOBS_AGENT_TYPE"] = client

    try:
        data = _payload()
        from djobs import lifecycle

        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "session-start": lifecycle.session_start,
            "post": lifecycle.post_tool_use,
            "post-failure": lifecycle.post_tool_failure,
            "pre-compact": lifecycle.pre_compact,
            "session-end": lifecycle.session_end,
            "user-prompt": lifecycle.user_prompt_submit,
            "pre": lifecycle.pre_tool_use,
            "stop": lifecycle.stop,
        }
        result = handlers[args.event](data, agent_type=client)
    except Exception:
        result = {}

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
