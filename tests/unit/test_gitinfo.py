"""Unit tests for read-only git working-tree inspection (``djobs.core.gitinfo``)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from djobs.core.gitinfo import _parse_porcelain, working_tree_changes


def test_parse_porcelain_handles_spaces_quotes_and_renames() -> None:
    output = "\n".join(
        [
            " M normal.py",
            "?? file with spaces.txt",
            'R  "old name.py" -> "new name.py"',
            "",
        ]
    )
    assert _parse_porcelain(output) == [
        "file with spaces.txt",
        "new name.py",
        "normal.py",
    ]


def test_rev_parse_false_is_not_treated_as_work_tree(monkeypatch) -> None:
    def _fake_git(_cwd: str, args: list[str]) -> tuple[bool, str]:
        assert args == ["rev-parse", "--is-inside-work-tree"]
        return True, "false\n"

    monkeypatch.setattr("djobs.core.gitinfo._run_git", _fake_git)

    result = working_tree_changes(".")
    assert result["is_git_repo"] is False
    assert "changed_files" not in result


def test_status_failure_reports_reason_without_claiming_zero_changes(monkeypatch) -> None:
    def _fake_git(_cwd: str, args: list[str]) -> tuple[bool, str]:
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return True, "true\n"
        if args == ["status", "--porcelain"]:
            return False, "fatal: index file corrupt"
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr("djobs.core.gitinfo._run_git", _fake_git)

    result = working_tree_changes(".")
    assert result == {"is_git_repo": True, "reason": "fatal: index file corrupt"}


def test_diff_failure_still_reports_status_changes(monkeypatch) -> None:
    def _fake_git(_cwd: str, args: list[str]) -> tuple[bool, str]:
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return True, "true\n"
        if args == ["status", "--porcelain"]:
            return True, " M tracked.py\n?? new.py\n"
        if args == ["diff", "--shortstat", "HEAD"]:
            return False, "fatal: ambiguous argument 'HEAD'"
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr("djobs.core.gitinfo._run_git", _fake_git)

    result = working_tree_changes(".")
    assert result["is_git_repo"] is True
    assert result["changed_files"] == ["new.py", "tracked.py"]
    assert result["changed_file_count"] == 2
    assert result["diff_summary"] is None


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return False


requires_git = pytest.mark.skipif(not _git_available(), reason="git not installed")


def test_non_git_directory_reports_not_repo(tmp_path: Path) -> None:
    result = working_tree_changes(str(tmp_path))
    assert result["is_git_repo"] is False
    assert "reason" in result


@requires_git
def test_clean_repo_has_no_changes(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")

    result = working_tree_changes(str(tmp_path))
    assert result["is_git_repo"] is True
    assert result["changed_files"] == []


@requires_git
def test_modified_and_untracked_files_are_reported(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "tracked.txt").write_text("v1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")

    # Modify a tracked file and add an untracked one.
    (tmp_path / "tracked.txt").write_text("v2\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("brand new\n", encoding="utf-8")

    result = working_tree_changes(str(tmp_path))
    assert result["is_git_repo"] is True
    assert "tracked.txt" in result["changed_files"]
    assert "new.txt" in result["changed_files"]
    assert result["changed_file_count"] == 2


@requires_git
def test_renamed_path_uses_new_name(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "old.txt").write_text("content\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    _git(tmp_path, "mv", "old.txt", "renamed.txt")

    result = working_tree_changes(str(tmp_path))
    assert "renamed.txt" in result["changed_files"]
