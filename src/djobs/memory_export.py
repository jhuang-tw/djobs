"""New-file-only export into an explicitly selected Git worktree.

An exported file is a user-owned copy, never a second canonical memory store.
No host prompt directories are writable through this helper. This boundary is
not a sandbox against a privileged process concurrently rewriting directories.
"""

from __future__ import annotations

import difflib
import os
import re
import stat
import subprocess
from pathlib import Path, PureWindowsPath

from djobs.memory_artifacts import ArtifactError, safe_text

_FORBIDDEN = {
    ".git",
    ".agents",
    ".claude",
    ".codex",
    ".venv",
    ".ssh",
    ".aws",
    "agents.md",
    "claude.md",
}


def export_target(root: str, relative: str) -> Path:
    if safe_text(relative, 1000, required=True) != relative:
        raise ArtifactError("exact_export_destination_required")
    path = Path(relative.replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(relative).drive or ":" in relative:
        raise ArtifactError("relative_export_destination_required")
    if any(
        part in {"", ".", ".."} or part.casefold() in _FORBIDDEN or part.rstrip(" .") != part
        for part in path.parts
    ):
        raise ArtifactError("unsafe_export_destination")
    if any(
        re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0])
        for part in path.parts
    ):
        raise ArtifactError("reserved_export_destination")
    if path.suffix.casefold() != ".md":
        raise ArtifactError("markdown_export_required")
    canonical = Path(root).resolve(strict=True)
    target = canonical / path
    current = canonical
    for component in path.parts[:-1]:
        current /= component
        info = current.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ArtifactError("export_parent_must_be_existing_real_directory")
    if not target.parent.resolve(strict=True).is_relative_to(canonical):
        raise ArtifactError("export_destination_outside_worktree")
    if target.exists() or target.is_symlink():
        raise ArtifactError("export_destination_already_exists")
    git = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(canonical), "rev-parse", "--show-toplevel"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    if git.returncode or Path(git.stdout.strip()).resolve() != canonical:
        raise ArtifactError("git_worktree_export_required")
    return target


def preview_diff(relative: str, text: str) -> str:
    return "".join(
        difflib.unified_diff(
            [], text.splitlines(keepends=True), fromfile="/dev/null", tofile=relative
        )
    )


def write_new_export(target: Path, text: str) -> int:
    encoded = text.encode("utf-8")
    if len(encoded) > 64000:
        raise ArtifactError("export_content_bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        # Only this newly created file belongs to this failed export attempt.
        target.unlink(missing_ok=True)
        raise
    return len(encoded)
