from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from djobs.workspace import normalize_path, path_key, resolve_workspace


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _old_workspace_id(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
    return f"repo:{digest}"


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


def test_cross_shell_reads_include_pre_normalization_workspace_ids(monkeypatch) -> None:
    windows = resolve_workspace(cwd=r"C:\Work\Repo")
    wsl = resolve_workspace(cwd="/mnt/c/Work/Repo")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    git_bash = resolve_workspace(cwd="/c/Work/Repo")

    old_wsl = _old_workspace_id("/mnt/c/Work/Repo")
    old_git_bash = _old_workspace_id("/c/Work/Repo")
    assert old_wsl in windows.correlation_ids
    assert old_git_bash in windows.correlation_ids
    assert old_wsl in wsl.correlation_ids
    assert old_git_bash in git_bash.correlation_ids


def test_git_bash_drive_path_uses_windows_identity(monkeypatch) -> None:
    monkeypatch.setenv("MSYSTEM", "MINGW64")

    assert path_key("/c/Work/Repo") == path_key(r"C:\Work\Repo")


def test_drive_root_aliases_remain_canonical(monkeypatch) -> None:
    assert path_key("/mnt/c") == "c:/"
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    assert path_key("/c") == "c:/"


def test_workspace_keeps_legacy_subdirectory_correlation_variant(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    child = root / "src" / "feature"
    child.mkdir(parents=True)

    workspace = resolve_workspace(cwd=str(child))

    assert normalize_path(child) in workspace.correlation_ids


def test_unicode_git_workspace_preserves_identity_under_cp950_locale(tmp_path, monkeypatch):
    import threading

    from djobs.workspace import _git_output

    root = _git_repo(tmp_path / "中文-✅")
    import locale

    errors = []
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp950")
    if hasattr(subprocess, "_text_encoding"):
        monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp950")
    monkeypatch.setattr(
        threading, "excepthook", lambda args: errors.append(args.exc_type.__name__)
    )
    value = _git_output(str(root), "rev-parse", "--show-toplevel")
    assert value is not None and path_key(value) == path_key(root)
    assert not errors


def test_invalid_utf8_git_identity_does_not_produce_replacement_path(monkeypatch):
    from djobs.workspace import _git_output

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, bytes([255]), b"")
    )
    assert _git_output(".", "rev-parse", "--show-toplevel") is None


def test_git_identity_probe_never_inherits_mcp_stdio(monkeypatch):
    from djobs.workspace import _git_output

    observed = {}

    def fake_run(*args, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(args, 0, b"C:/repo\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_output(".", "rev-parse", "--show-toplevel") == "C:/repo"
    assert observed["stdin"] is subprocess.DEVNULL
