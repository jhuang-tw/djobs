from __future__ import annotations

import argparse
import json
from pathlib import Path

import djobs.cli as cli
import djobs.entrypoint as entrypoint


def _args(target: Path) -> argparse.Namespace:
    return argparse.Namespace(
        full_approve=False,
        print=False,
        force=True,
        output=str(target),
        db=None,
        use_global=False,
        command=None,
        python="/custom/python",
        portable=False,
        write_instructions=False,
    )


def test_install_mcp_preserves_other_servers(tmp_path: Path) -> None:
    target = tmp_path / "mcp.json"
    target.write_text(
        json.dumps(
            {
                "servers": {"other": {"type": "stdio", "command": "other-mcp"}},
                "inputs": [{"id": "keep-me"}],
            }
        ),
        encoding="utf-8",
    )

    entrypoint._cmd_install_mcp_high_level(_args(target), cli)

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["servers"]["other"]["command"] == "other-mcp"
    assert data["inputs"] == [{"id": "keep-me"}]
    assert data["servers"]["djobs"]["autoApprove"] == [
        "sync_workspace",
        "resume_delta",
    ]


def test_zero_config_instructions_keep_observation_and_ownership_separate() -> None:
    body = entrypoint._ZERO_CONFIG_INSTRUCTIONS_BODY

    assert "client-neutral" in body
    assert "never infer task" in body
    assert "Do not treat a session start" in body
    assert "sync_workspace()" in body
    assert "checkpoint(summary" in body
    assert "handoff(task_id" in body
    assert "Never hijack the user's intent." in body
    assert "untrusted data" in body
    assert "enqueue_batch" not in body
