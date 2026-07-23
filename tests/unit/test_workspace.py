from __future__ import annotations

import subprocess
from pathlib import Path

from djobs.workspace import normalize_path, path_key, resolve_workspace


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_workspace_resolver_prefers_mcp_roots(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "root")
    other = _git_repo(tmp_path / "other")

    workspace = resolve_workspace(roots=[root.as_uri()], cwd=str(other))

    assert workspace.root == normalize_path(root)
    assert workspace.source == "mcp_roots"


def test_workspace_resolver_returns_git_root_from_subdirectory(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    child = root / "src" / "feature"
    child.mkdir(parents=True)

    workspace = resolve_workspace(cwd=str(child))

    assert workspace.root == normalize_path(root)


def test_windows_path_normalization_is_tolerant() -> None:
    expected = "c:/Work/Repo"

    assert normalize_path("C:\\Work\\Repo\\") == expected
    assert normalize_path("c:/Work/Repo/") == expected
    assert path_key(r"C:\Work\Repo") == path_key("c:/Work/Repo/")


def test_wsl_and_windows_paths_share_repository_identity() -> None:
    windows = resolve_workspace(cwd=r"C:\Work\Repo")
    wsl = resolve_workspace(cwd="/mnt/c/Work/Repo")

    assert path_key(wsl.root) == path_key(windows.root)
    assert wsl.workspace_id == windows.workspace_id
    assert "c:/work/repo" in wsl.correlation_ids


def test_git_bash_drive_path_uses_windows_identity(monkeypatch) -> None:
    monkeypatch.setenv("MSYSTEM", "MINGW64")

    assert path_key("/c/Work/Repo") == path_key(r"C:\Work\Repo")


def test_workspace_keeps_legacy_subdirectory_correlation_variant(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    child = root / "src" / "feature"
    child.mkdir(parents=True)

    workspace = resolve_workspace(cwd=str(child))

    assert normalize_path(child) in workspace.correlation_ids
