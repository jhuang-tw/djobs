"""Bounded, short-lived SQLite snapshots with no source database side effects.

SQLite mode=ro may create WAL/SHM files. Opening the live source as immutable
would incorrectly ignore a live WAL. Instead, copy only stable DB/WAL/journal
bytes into a private temporary directory, let SQLite handle its own file format
there, and remove the copy on close. No persistent projection or index is made.
"""

from __future__ import annotations

import hashlib
import sqlite3
import stat
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

_MAX_BYTES = 64 * 1024 * 1024
_CAPTURE_SECONDS = 0.5
_CHUNK = 1024 * 1024


class SnapshotUnavailableError(RuntimeError):
    """A bounded, non-sensitive reason; callers continue coding without memory."""


class ReadOnlySnapshot(sqlite3.Connection):
    _temporary: Any = None

    def close(self) -> None:
        try:
            super().close()
        finally:
            temporary, self._temporary = self._temporary, None
            if temporary is not None:
                temporary.cleanup()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()


def _generation(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise SnapshotUnavailableError("non_regular_database_file")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _copy_or_hash(source: Path, destination: Path | None, *, deadline: float) -> str:
    digest = hashlib.sha256()
    size = 0
    output = None
    try:
        if destination is not None:
            output = destination.open("xb")
        with source.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > _MAX_BYTES or time.monotonic() > deadline:
                    raise SnapshotUnavailableError("snapshot_capture_bound")
                digest.update(chunk)
                if output is not None:
                    output.write(chunk)
    finally:
        if output is not None:
            output.close()
    return digest.hexdigest()


def connect_read_only(path: Path) -> sqlite3.Connection | None:
    """Capture a stable read view, or explicitly fail open without creating source files.

    Both generation metadata and content digests must stay unchanged across the
    capture. A busy/oversized source is not reported as an empty memory store.
    The snapshot boundary is the capture, not a promise of future live state.
    """

    if not path.exists():
        return None
    source = path.expanduser().resolve(strict=True)
    paths = [
        source,
        source.with_name(source.name + "-wal"),
        source.with_name(source.name + "-journal"),
    ]
    deadline = time.monotonic() + _CAPTURE_SECONDS
    before = [_generation(item) for item in paths]
    if before[0] is None:
        return None
    if sum(item[2] for item in before if item is not None) > _MAX_BYTES:
        raise SnapshotUnavailableError("snapshot_capture_bound")
    temporary = tempfile.TemporaryDirectory(prefix="djobs-readonly-")
    connection: ReadOnlySnapshot | None = None
    try:
        snapshot = Path(temporary.name) / "memory.db"
        targets = [
            snapshot,
            snapshot.with_name("memory.db-wal"),
            snapshot.with_name("memory.db-journal"),
        ]
        copied = [
            _copy_or_hash(item, target, deadline=deadline) if generation is not None else None
            for item, target, generation in zip(paths, targets, before, strict=True)
        ]
        if before != [_generation(item) for item in paths]:
            raise SnapshotUnavailableError("source_changed_during_capture")
        verified = [
            _copy_or_hash(item, None, deadline=deadline) if generation is not None else None
            for item, generation in zip(paths, before, strict=True)
        ]
        if copied != verified or before != [_generation(item) for item in paths]:
            raise SnapshotUnavailableError("source_changed_during_capture")
        if time.monotonic() > deadline:
            raise SnapshotUnavailableError("snapshot_capture_bound")
        # SQLite may recover WAL/journal state ONLY in the private copy. Do not
        # hand-edit pages, ignore WAL, or open the live source with SQLite here.
        connection = sqlite3.connect(
            snapshot.as_uri() + "?mode=rw",
            uri=True,
            timeout=0.05,
            check_same_thread=False,
            factory=ReadOnlySnapshot,
        )
        connection._temporary = temporary
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        else:
            temporary.cleanup()
        raise
