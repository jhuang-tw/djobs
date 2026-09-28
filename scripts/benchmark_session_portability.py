#!/usr/bin/env python3
"""Original offline text portability fixture. No real user sessions or native resume claims."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

from djobs.artifacts import ArtifactMemory
from djobs.memory_review import ReviewGate
from djobs.observations import clear_workspace_memory, search_observations
from djobs.session_adapters import ADAPTERS
from djobs.session_memory import SessionMemory
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/memory/session_formats.json"


def run(repository: Any = None) -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="djobs-session-benchmark-") as directory:
        root = Path(directory)
        repo = repository or SQLiteJobRepository.from_path(root / "memory.db")
        workspace = Workspace(
            root=str(root),
            workspace_id="repo:session-benchmark",
            checkout_id="repo:session-benchmark",
            repo_family_id="family:session-benchmark",
            correlation_ids=("repo:session-benchmark",),
            memory_correlation_ids=("family:session-benchmark", "repo:session-benchmark"),
            source="synthetic",
        )
        memory = SessionMemory(repo, workspace)
        checks = {}
        imports, elapsed = [], []
        before = memory.list_imports()["count"]
        try:
            for harness in ("claude", "codex", "opencode"):
                records = data[harness]
                text = (
                    "\n".join(json.dumps(row) for row in records)
                    if isinstance(records, list)
                    else json.dumps(records)
                )
                name = harness + (".json" if harness == "opencode" else ".jsonl")
                (root / name).write_text(text, encoding="utf-8")
                preview = memory.preview(str(root), name, harness, ["u-one", "a-one"])
                checks[harness + "_preview_is_no_write"] = memory.list_imports()["count"] == len(
                    imports
                )
                started = time.perf_counter()
                result = memory.import_session(
                    str(root),
                    name,
                    harness,
                    ["u-one", "a-one"],
                    expected_hash=preview["binding_hash"],
                    expected_family=workspace.repo_family_id,
                    confirm=True,
                )
                elapsed.append((time.perf_counter() - started) * 1000)
                imports.append(result["import"]["id"])
                checks[harness + "_quarantined"] = (
                    result["import"]["status"] == "imported_unverified"
                )
                repeated = memory.import_session(
                    str(root),
                    name,
                    harness,
                    ["u-one", "a-one"],
                    expected_hash=preview["binding_hash"],
                    expected_family=workspace.repo_family_id,
                    confirm=True,
                )
                checks[harness + "_deduplicated"] = (
                    repeated["duplicate"] and not repeated["changed"]
                )
                bundle = memory.export(imports[-1])["document"]
                roundtrip = ADAPTERS["djobs"].parse(json.dumps(bundle).encode())
                checks[harness + "_text_roundtrip"] = roundtrip["messages"] == bundle["messages"]
                serialized = json.dumps(bundle)
                checks[harness + "_privacy_and_no_execution_state"] = all(
                    value not in serialized
                    for value in (
                        "synthetic-credential-fixture",
                        "DO_NOT_EXECUTE",
                        "NEVER_INSTALL_THIS",
                    )
                )
            after = memory.list_imports()["count"]
            memory.review(
                imports[0],
                ReviewGate(lambda request: "accept", reviewer="synthetic-session-review"),
            )
            checks["review_is_reference_only"] = (
                memory.get(imports[0])["status"] == "reviewed_reference"
            )
            checks["normal_resume_stays_empty"] = (
                not search_observations(repo, workspace, "保留加號")
                and not ArtifactMemory(repo, workspace).tree()["memories"]
            )
            checks["audit_exposes_imports"] = (
                len(ArtifactMemory(repo, workspace).tree(exposure="audit")["memories"]) == 3
            )
            with memory.store.transaction() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM jobs")
                checks["tasks_untouched"] = cursor.fetchone()["n"] == 0
            clear_workspace_memory(repo, workspace)
            checks["clear_removes_imported_content"] = not memory.list_imports()["memories"]
            ordered = sorted(elapsed)
            return {
                "benchmark": "synthetic-session-portability-v1",
                "checks": checks,
                "pass": all(checks.values()),
                "before": {"imported_sessions": before},
                "after": {"imported_sessions": after, "selected_text_messages": after * 2},
                "import_p50_ms": ordered[len(ordered) // 2],
                "import_p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
                "model_calls": 0,
                "external_network_calls": 0,
                "native_resumption_supported": False,
                "generation": "not_run",
                "answer_judge": "not_run",
                "scope": "Selected redacted text and provenance only; not native execution state",
            }
        finally:
            if repository is None:
                repo.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    encoded = json.dumps(result, ensure_ascii=True, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
