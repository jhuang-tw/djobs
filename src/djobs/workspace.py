"""Zero-configuration repository and agent-session resolution."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import unquote, urlparse

from djobs.core.correlation import correlation_id_variants

_PROCESS_SESSION_ID = uuid.uuid4().hex


@dataclass(frozen=True)
class Workspace:
    """Canonical identity for one repository checkout opened by an MCP client.

    ``workspace_id``/``checkout_id`` remain checkout-scoped so explicit task
    ownership never leaks across worktrees. ``repo_family_id`` is stable across
    worktrees and compatible clones and is used by passive project memory.
    """

    root: str
    workspace_id: str
    correlation_ids: tuple[str, ...]
    source: str
    repo_family_id: str = ""
    checkout_id: str = ""
    memory_correlation_ids: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        normalized = self.root.rstrip("/\\")
        return normalized.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or normalized


@dataclass(frozen=True)
class AgentSession:
    """Stable identity for one coding-agent process/session in one workspace."""

    agent_type: str
    session_id: str
    agent_id: str


def _root_value(value: Any) -> str | None:
    if isinstance(value, (str, os.PathLike)):
        raw = os.fspath(value)
    elif isinstance(value, dict):
        raw = value.get("uri") or value.get("path")
    else:
        raw = getattr(value, "uri", None) or getattr(value, "path", None)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path
    return text


def normalize_path(path: str | os.PathLike[str]) -> str:
    """Normalize path spellings without requiring the path to exist.

    Backslashes and forward slashes compare equally, trailing separators are
    ignored, and a Windows drive letter is case-insensitive.
    """

    raw = os.fspath(path).strip().replace("\\", "/")
    if not raw:
        return raw
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
        drive = raw[0].lower()
        rest = raw[2:]
        raw = f"{drive}:{rest}"
    while len(raw) > 1 and raw.endswith("/"):
        if len(raw) == 3 and raw[1] == ":":
            break
        raw = raw[:-1]
    return raw


def _mounted_windows_alias(normalized: str) -> str | None:
    """Translate common WSL/MSYS spellings into one Windows identity key."""

    parts = normalized.split("/")
    if (
        len(parts) >= 3
        and parts[0] == ""
        and parts[1].casefold() == "mnt"
        and len(parts[2]) == 1
        and parts[2].isalpha()
    ):
        suffix = "/".join(parts[3:])
        return f"{parts[2].lower()}:/{suffix}" if suffix else f"{parts[2].lower()}:/"

    is_msys = bool(os.environ.get("MSYSTEM") or os.environ.get("CYGWIN"))
    if (
        is_msys
        and len(parts) >= 2
        and parts[0] == ""
        and len(parts[1]) == 1
        and parts[1].isalpha()
    ):
        suffix = "/".join(parts[2:])
        return f"{parts[1].lower()}:/{suffix}" if suffix else f"{parts[1].lower()}:/"
    return None


def path_key(path: str | os.PathLike[str]) -> str:
    """Return the cross-shell comparison key used for repository identity."""

    normalized = normalize_path(path)
    alias = _mounted_windows_alias(normalized)
    if alias is not None:
        return alias.casefold()
    if len(normalized) >= 2 and normalized[1] == ":":
        return normalized.casefold()
    return normalized


def _workspace_id(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"repo:{digest}"


def _family_id(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"family:{digest}"


def _cross_shell_aliases(path: str | os.PathLike[str]) -> set[str]:
    """Return path spellings used by native Windows, WSL, and Git Bash.

    These aliases are used only for identity compatibility and reads. The actual
    workspace root remains the native path so Git commands run in the right OS.
    """

    normalized = normalize_path(path)
    aliases = {normalized, path_key(normalized)}
    identity = path_key(normalized)
    if len(identity) >= 3 and identity[1:3] == ":/" and identity[0].isalpha():
        drive = identity[0].lower()
        suffix = normalized[3:] if _is_windows_path(normalized) else identity[3:]
        aliases.add(f"/mnt/{drive}/{suffix}".rstrip("/") if suffix else f"/mnt/{drive}")
        aliases.add(f"/{drive}/{suffix}".rstrip("/") if suffix else f"/{drive}")
    return aliases


def _is_windows_path(path: str) -> bool:
    return len(path) >= 2 and path[1] == ":" and PureWindowsPath(path).drive != ""


def _git_output(path: str, *args: str, timeout: float = 2.0) -> str | None:
    normalized = normalize_path(path)
    if _is_windows_path(normalized) and os.name != "nt":
        return None
    try:
        result = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _git_root(path: str) -> str:
    normalized = normalize_path(path)
    mounted_alias = _mounted_windows_alias(normalized)
    if mounted_alias is not None and os.name == "nt":
        normalized = mounted_alias
        path = mounted_alias
    if _is_windows_path(normalized) and os.name != "nt":
        return normalized

    candidate = Path(path).expanduser()
    with suppress(OSError):
        candidate = candidate.resolve(strict=False)
    if candidate.is_file():
        candidate = candidate.parent

    resolved = _git_output(str(candidate), "rev-parse", "--show-toplevel")
    if resolved:
        return normalize_path(resolved)

    current = candidate
    for parent in (current, *current.parents):
        if (parent / ".git").exists():
            return normalize_path(str(parent))
    return normalize_path(str(candidate))


def _select_root(roots: list[str], cwd: str | None) -> str | None:
    if not roots:
        return None
    if cwd:
        cwd_key = path_key(_git_root(cwd))
        containing = [root for root in roots if cwd_key == path_key(root)]
        if containing:
            return max(containing, key=len)
    return roots[0]


def _normalize_remote(remote: str) -> str:
    """Normalize common HTTPS/SSH/scp Git remotes into one family key."""

    value = remote.strip().replace("\\", "/")
    scp_match = re.match(r"^(?:[^@]+@)?([^:]+):(.+)$", value)
    if scp_match and "://" not in value:
        value = f"ssh://{scp_match.group(1)}/{scp_match.group(2)}"
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        host = (parsed.hostname or parsed.netloc).casefold()
        path = parsed.path.strip("/")
        value = f"{host}/{path}"
    value = value.removesuffix(".git").rstrip("/")
    return value.casefold()


def _repository_family_identity(root: str, checkout_identity: str) -> str:
    remote = _git_output(root, "config", "--get", "remote.origin.url")
    roots = _git_output(root, "rev-list", "--max-parents=0", "--all", timeout=5.0)
    root_commit = sorted(roots.splitlines())[0] if roots else ""
    common_dir = _git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")

    if remote:
        identity = f"remote:{_normalize_remote(remote)}"
        if root_commit:
            identity += f"|root:{root_commit}"
        return identity
    if common_dir:
        identity = f"common:{path_key(common_dir)}"
        if root_commit:
            identity += f"|root:{root_commit}"
        return identity
    return f"checkout:{checkout_identity}"


def resolve_workspace(
    *,
    roots: list[Any] | tuple[Any, ...] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    server_cwd: str | os.PathLike[str] | None = None,
) -> Workspace:
    """Resolve a checkout plus a repository-family identity.

    Explicit tasks remain checkout-scoped. Passive memory is written to a stable
    repository-family scope so sibling worktrees and compatible clones can reuse
    architecture decisions and prior failures without sharing live ownership.
    """

    root_candidates = [value for item in roots or () if (value := _root_value(item))]
    resolved_roots = [_git_root(value) for value in root_candidates]
    selected = _select_root(resolved_roots, os.fspath(cwd) if cwd is not None else None)

    if selected is not None:
        root = selected
        source = "mcp_roots"
    elif cwd is not None and os.fspath(cwd).strip():
        root = _git_root(os.fspath(cwd))
        source = "request_cwd"
    else:
        fallback = os.fspath(server_cwd) if server_cwd is not None else os.getcwd()
        root = _git_root(fallback)
        source = "server_cwd"

    canonical = normalize_path(root)
    identity = path_key(canonical)
    checkout_id = _workspace_id(identity)
    family_identity = _repository_family_identity(canonical, identity)
    repo_family_id = _family_id(family_identity)

    # Reads remain compatible with old clients that stored request cwd, a Git
    # subdirectory, alternate shell spellings, or the old deterministic hash.
    legacy_candidates = [canonical, identity, *root_candidates, *(resolved_roots or [])]
    if cwd is not None and os.fspath(cwd).strip():
        legacy_candidates.append(normalize_path(os.fspath(cwd)))
    if server_cwd is not None and os.fspath(server_cwd).strip():
        legacy_candidates.append(normalize_path(os.fspath(server_cwd)))

    aliases: set[str] = set()
    for value in legacy_candidates:
        aliases.update(_cross_shell_aliases(value))

    compatible: set[str] = {checkout_id, canonical, identity}
    for alias in aliases:
        compatible.update(correlation_id_variants(alias))
        compatible.add(_workspace_id(alias.casefold() if _is_windows_path(alias) else alias))

    memory_compatible = set(compatible)
    memory_compatible.add(repo_family_id)
    return Workspace(
        root=canonical,
        workspace_id=checkout_id,
        checkout_id=checkout_id,
        repo_family_id=repo_family_id,
        correlation_ids=tuple(sorted(compatible)),
        memory_correlation_ids=tuple(sorted(memory_compatible)),
        source=source,
    )


def shared_db_path() -> Path:
    """Return the one shared local-first database used by zero-config setup."""

    configured = os.environ.get("DJOBS_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".djobs" / "global.db"


def resolve_agent_session(
    workspace: Workspace,
    *,
    agent_type: str | None = None,
    session_id: str | None = None,
) -> AgentSession:
    """Infer a safe client/session identity while allowing explicit overrides."""

    detected_type = (agent_type or os.environ.get("DJOBS_AGENT_TYPE") or "").strip().lower()
    detected_session = (session_id or os.environ.get("DJOBS_AGENT_SESSION_ID") or "").strip()

    candidates = (
        ("codex", "CODEX_THREAD_ID"),
        ("codex", "CODEX_SESSION_ID"),
        ("claude", "CLAUDE_CODE_SESSION_ID"),
        ("claude", "CLAUDE_SESSION_ID"),
    )
    for candidate_type, variable in candidates:
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        if not detected_type:
            detected_type = candidate_type
        if not detected_session:
            detected_session = value
        break

    if not detected_type:
        client = os.environ.get("MCP_CLIENT_NAME", "agent").strip().lower()
        detected_type = client or "agent"
    if not detected_session:
        detected_session = _PROCESS_SESSION_ID

    safe_type = "".join(char for char in detected_type if char.isalnum() or char in "-_")
    safe_type = safe_type or "agent"
    safe_session = hashlib.sha256(detected_session.encode("utf-8")).hexdigest()[:16]
    checkout_id = workspace.checkout_id or workspace.workspace_id
    suffix = checkout_id.rsplit(":", 1)[-1][:10]
    return AgentSession(
        agent_type=safe_type,
        session_id=detected_session,
        agent_id=f"{safe_type}:{safe_session}:{suffix}",
    )
