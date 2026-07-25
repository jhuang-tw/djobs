"""Create a temporary repository and show a two-session djobs memory recovery.

This example uses a temporary SQLite database and does not modify your normal ~/.djobs state.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from djobs.handoff import sync_workspace
from djobs.observations import record_observation
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_agent_session, resolve_workspace


def _run(*args: str) -> None:
    subprocess.run(list(args), check=True, stdout=subprocess.DEVNULL)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="djobs-memory-example-") as temp:
        root = Path(temp) / "oauth-service"
        root.mkdir()
        _run("git", "init", "-q", str(root))
        _run("git", "-C", str(root), "config", "user.name", "djobs example")
        _run("git", "-C", str(root), "config", "user.email", "example@example.invalid")
        (root / "callback.py").write_text("def parse_state(value):\n    return value\n", encoding="utf-8")
        _run("git", "-C", str(root), "add", ".")
        _run("git", "-C", str(root), "commit", "-qm", "initial callback parser")

        database = Path(temp) / "memory.db"
        os.environ["DJOBS_DB"] = str(database)
        repository = SQLiteJobRepository.from_path(database)
        workspace = resolve_workspace(cwd=str(root))
        first_session = resolve_agent_session(
            workspace,
            agent_type="example",
            session_id="session-1",
        )

        record_observation(
            repository,
            workspace,
            first_session,
            "user_intent",
            "Fix the OAuth callback loop without changing the public API; preserve '+' in state.",
        )
        record_observation(
            repository,
            workspace,
            first_session,
            "tool_failure",
            "Normalization removed '+' from the state parameter.",
        )
        record_observation(
            repository,
            workspace,
            first_session,
            "session_capsule",
            "Goal: fix the callback loop || Progress: parser updated || Next: run integration tests",
            metadata={
                "goal": "Fix the OAuth callback loop without changing the public API.",
                "progress": ["Updated the callback parser."],
                "failures": ["Normalization removed '+' from state."],
                "next": "Run the callback integration tests.",
            },
        )
        repository.close()

        recovered = json.loads(
            sync_workspace(
                cwd=str(root),
                agent_type="example",
                session_id="session-2",
                query="Continue the OAuth callback fix",
                context_tier="evidence",
            )
        )

        print("Session 2 recovery:\n")
        print(json.dumps(recovered, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
