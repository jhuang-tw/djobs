"""Pure virtual context addresses. No disk tree, cache, or lifecycle writer."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

from djobs.memory_artifacts import ArtifactError

FOLDERS = {
    "episode": "episodes",
    "fact": "facts",
    "experience": "experiences",
    "lesson": "lessons",
    "skill_candidate": "skills",
    "skill": "skills",
    "imported_session": "imports",
}


def context_uri(family: str, *, kind: str | None = None, artifact_id: str | None = None) -> str:
    if not isinstance(family, str) or not 1 <= len(family) <= 300:
        raise ArtifactError("invalid_context_repository")
    root = "djobs://repo/" + quote(family, safe="") + "/"
    if kind is None:
        if artifact_id is not None:
            raise ArtifactError("context_category_required")
        return root
    if kind not in FOLDERS:
        raise ArtifactError("unsupported_context_category")
    root += FOLDERS[kind] + "/"
    if artifact_id is not None:
        prefix = "imp" if kind == "imported_session" else "mem"
        if not isinstance(artifact_id, str) or not re.fullmatch(
            prefix + r"_[0-9a-f]{32}", artifact_id
        ):
            raise ArtifactError("invalid_context_identifier")
        root += artifact_id
    return root


def parse_context_uri(value: str, family: str) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or len(value) > 1600:
        raise ArtifactError("invalid_context_uri")
    parsed = urlsplit(value)
    if parsed.scheme != "djobs" or parsed.netloc != "repo" or parsed.query or parsed.fragment:
        raise ArtifactError("invalid_context_uri")
    parts = parsed.path.split("/")
    if len(parts) not in {3, 4} or parts[0] or unquote(parts[1]) != family:
        raise ArtifactError("context_repository_mismatch")
    folder = parts[2] or None
    if folder is None:
        if value != context_uri(family):
            raise ArtifactError("invalid_context_uri")
        return None, None
    kinds = {name: kind for kind, name in FOLDERS.items()}
    if folder not in kinds or len(parts) != 4:
        raise ArtifactError("invalid_context_uri")
    artifact_id = parts[3] or None
    if value != context_uri(family, kind=kinds[folder], artifact_id=artifact_id):
        raise ArtifactError("noncanonical_context_uri")
    return folder, artifact_id
