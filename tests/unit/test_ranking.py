from __future__ import annotations

import json
import subprocess
from pathlib import Path

from djobs.ranking import rank_memory_rows


def _git_repo(path: Path) -> tuple[str, str]:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (path / "app.py").write_text("print('base')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)
    branch = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return branch, head


def _row(
    memory_id: str,
    summary: str,
    *,
    created_at: str,
    metadata: dict[str, object] | None = None,
    event_type: str = "tool_result",
) -> dict[str, object]:
    return {
        "id": memory_id,
        "agent_type": "test",
        "event_type": event_type,
        "tool_name": "edit",
        "summary": summary,
        "metadata_json": json.dumps(metadata or {}),
        "created_at": created_at,
    }


def test_ranking_explains_branch_commit_and_path_affinity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    branch, head = _git_repo(root)
    rows = [
        _row(
            "same-branch",
            "OAuth callback parser updated",
            created_at="2026-01-01T00:00:00+00:00",
            metadata={
                "branch": branch,
                "commit_sha": head,
                "affected_files": ["src/auth/callback.py"],
                "source": "git_snapshot",
            },
        ),
        _row(
            "other-branch",
            "OAuth callback parser updated elsewhere",
            created_at="2026-01-02T00:00:00+00:00",
            metadata={"branch": "unrelated", "affected_files": ["docs/auth.md"]},
        ),
    ]

    ranked = rank_memory_rows(
        rows,
        query="OAuth src/auth/callback.py",
        workspace_root=str(root),
        limit=5,
    )

    assert ranked[0].row["id"] == "same-branch"
    assert {"same_branch", "commit_ancestor", "affected_path"}.issubset(ranked[0].matched_by)


def test_ranking_excludes_inactive_and_deduplicates_summaries_deterministically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _git_repo(root)
    rows = [
        _row(
            "newest",
            "Parser compatibility result",
            created_at="2026-01-03T00:00:00+00:00",
        ),
        _row(
            "duplicate",
            "Parser compatibility result",
            created_at="2026-01-02T00:00:00+00:00",
        ),
        _row(
            "resolved",
            "Parser compatibility failure",
            created_at="2026-01-04T00:00:00+00:00",
            metadata={"memory_status": "resolved"},
            event_type="tool_failure",
        ),
    ]

    first = rank_memory_rows(
        rows, query="parser compatibility", workspace_root=str(root), limit=10
    )
    second = rank_memory_rows(
        list(reversed(rows)),
        query="parser compatibility",
        workspace_root=str(root),
        limit=10,
    )

    assert [item.row["id"] for item in first] == ["newest"]
    assert [item.row["id"] for item in second] == ["newest"]
    assert first[0].score == second[0].score
    assert first[0].matched_by == second[0].matched_by


def test_unicode_branch_affinity_is_not_decoded_with_terminal_locale(tmp_path, monkeypatch):
    import threading

    from djobs.ranking import _git_value

    root = tmp_path / "unicode-branch"
    _git_repo(root)
    branch = "feature-中文-✅"
    subprocess.run(["git", "-C", str(root), "branch", "-m", branch], check=True)
    import locale

    errors = []
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp950")
    if hasattr(subprocess, "_text_encoding"):
        monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp950")
    monkeypatch.setattr(
        threading, "excepthook", lambda args: errors.append(args.exc_type.__name__)
    )
    assert _git_value(str(root), "branch", "--show-current") == branch
    assert not errors


def test_invalid_utf8_branch_has_no_affinity_instead_of_invented_text(monkeypatch):
    from djobs.ranking import _git_value

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, bytes([255]), b"")
    )
    assert _git_value(".", "branch", "--show-current") == ""
