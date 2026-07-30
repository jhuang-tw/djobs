"""Observable fail-open diagnostics for automatic djobs integrations."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from djobs.privacy import redact
from djobs.storage.diagnostics import clear as clear_stored
from djobs.storage.diagnostics import list_recent as list_stored
from djobs.storage.diagnostics import record as record_stored


def record_failure(
    repo: Any,
    component: str,
    exc: BaseException,
    *,
    context: dict[str, Any] | None = None,
) -> None:
    """Best-effort diagnostic recording that must never mask the original failure."""

    try:
        redacted = redact(str(exc) or exc.__class__.__name__)
        safe_context: dict[str, Any] = {
            str(key)[:80]: redact(value).text[:240] for key, value in (context or {}).items()
        }
        if redacted.categories:
            safe_context["redaction_categories"] = list(redacted.categories)
        record_stored(
            repo,
            component=component[:120],
            error_type=exc.__class__.__name__[:120],
            message=redacted.text[:500],
            context=safe_context,
        )
    except Exception:
        return


def list_diagnostics(repo: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    return list_stored(repo, limit=limit)


def clear_diagnostics(repo: Any) -> int:
    return clear_stored(repo)


def record_shared_failure(
    component: str,
    exc: BaseException,
    *,
    context: dict[str, Any] | None = None,
) -> None:
    """Record against the shared local database when no repository handle survived."""

    repo = None
    try:
        from djobs.storage.sqlite import SQLiteJobRepository
        from djobs.workspace import shared_db_path

        repo = SQLiteJobRepository.from_path(shared_db_path(), busy_timeout_ms=100)
        record_failure(repo, component, exc, context=context)
    except Exception:
        return
    finally:
        if repo is not None:
            with suppress(Exception):
                repo.close()
