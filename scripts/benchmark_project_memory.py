#!/usr/bin/env python3
"""Deterministic recovery-context proxy for djobs repository memory.

This benchmark does not claim provider billing or model-quality results. It
compares a conservative "re-read every synthetic source file" recovery payload
with one query-aware ``sync_workspace`` response for the same repository.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from djobs import lifecycle
from djobs.handoff import sync_workspace
from djobs.mcp_server import close_configured_queue


def estimate_tokens(text: str) -> int:
    """Return the repository's documented character-based proxy."""

    return max(1, math.ceil(len(text) / 4)) if text else 0


def create_fixture(root: Path, file_count: int) -> list[Path]:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    files: list[Path] = []
    for index in range(file_count):
        path = root / "src" / f"module_{index:02d}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f'"""Synthetic module {index}; public API compatibility matters."""',
            "",
            f"PUBLIC_NAME = 'module-{index:02d}'",
            "",
            "def normalize_callback(value: str) -> str:",
            '    """Preserve OAuth state characters while normalizing whitespace."""',
            "    return value.strip()",
            "",
        ]
        lines.extend(
            f"# implementation note {line:02d}: keep behavior stable" for line in range(32)
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        files.append(path)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=djobs benchmark",
            "-c",
            "user.email=benchmark@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return files


def seed_memory(root: Path) -> None:
    base = {"cwd": str(root), "session_id": "previous-session"}
    lifecycle.user_prompt_submit(
        {
            **base,
            "prompt": (
                "Fix the OAuth callback loop without changing the public auth API. "
                "Do not strip plus signs from the state parameter."
            ),
        },
        agent_type="copilot",
    )
    lifecycle.post_tool_failure(
        {
            **base,
            "tool_name": "bash",
            "tool_input": {"command": "pytest tests/test_oauth_callback.py"},
            "error": "state normalization removed plus signs; 2 tests failed",
        },
        agent_type="copilot",
    )
    lifecycle.post_tool_use(
        {
            **base,
            "tool_name": "edit",
            "tool_input": {"file_path": "src/module_03.py"},
            "tool_response": {
                "success": True,
                "message": "preserved plus signs; public API unchanged; integration test remains",
            },
        },
        agent_type="copilot",
    )
    lifecycle.pre_compact({**base, "trigger": "auto"}, agent_type="copilot")


def run(file_count: int) -> dict[str, Any]:
    previous_db = os.environ.get("DJOBS_DB")
    with tempfile.TemporaryDirectory(prefix="djobs-memory-benchmark-") as temp:
        try:
            temp_root = Path(temp)
            root = temp_root / "project"
            files = create_fixture(root, file_count)
            os.environ["DJOBS_DB"] = str(temp_root / "memory.db")
            seed_memory(root)

            baseline = "\n".join(path.read_text(encoding="utf-8") for path in files)
            query = "Continue the OAuth callback fix. What failed and what constraint must remain?"
            memory = sync_workspace(
                cwd=str(root),
                agent_type="copilot",
                session_id="new-session",
                query=query,
                max_items=6,
                token_budget=650,
                context_tier="resume",
            )
            decoded = json.loads(memory)
            baseline_tokens = estimate_tokens(baseline)
            memory_tokens = estimate_tokens(memory)
            reduction = 0.0 if baseline_tokens == 0 else 1 - memory_tokens / baseline_tokens
            return {
                "benchmark": "deterministic recovery-payload proxy",
                "disclaimer": "Not provider billing, latency, or model-quality measurement.",
                "fixture_files": len(files),
                "query": query,
                "baseline": {
                    "strategy": "re-read every synthetic source file",
                    "estimated_tokens": baseline_tokens,
                    "minimum_file_read_calls": len(files),
                },
                "djobs": {
                    "strategy": "one query-aware sync_workspace call",
                    "estimated_tokens": memory_tokens,
                    "mcp_calls": 1,
                    "returned_memories": sum(
                        1 for value in dict(decoded.get("resume") or {}).values() if value
                    ),
                },
                "proxy_reduction_percent": round(reduction * 100, 1),
            }
        finally:
            close_configured_queue()
            if previous_db is None:
                os.environ.pop("DJOBS_DB", None)
            else:
                os.environ["DJOBS_DB"] = previous_db


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=18)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(max(4, min(args.files, 100)))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print("djobs project-memory recovery benchmark")
    print(result["disclaimer"])
    print(f"Fixture: {result['fixture_files']} source files")
    print(
        "Baseline: "
        f"~{result['baseline']['estimated_tokens']} tokens, "
        f"at least {result['baseline']['minimum_file_read_calls']} file reads"
    )
    print(
        "djobs: "
        f"~{result['djobs']['estimated_tokens']} tokens, "
        f"{result['djobs']['mcp_calls']} MCP call"
    )
    print(f"Recovery-payload proxy reduction: {result['proxy_reduction_percent']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
