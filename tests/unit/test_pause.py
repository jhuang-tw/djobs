"""Unit tests for the pause-state helpers (``djobs.core.pause``)."""

from __future__ import annotations

from pathlib import Path

from djobs.core.pause import is_paused, pause_marker_path, set_paused


def test_default_not_paused(tmp_path: Path) -> None:
    assert is_paused(tmp_path / "q.db") is False


def test_set_paused_creates_marker(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    assert set_paused(db, True) is True
    assert is_paused(db) is True
    assert pause_marker_path(db).exists()


def test_pause_twice_reports_no_change(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    set_paused(db, True)
    assert set_paused(db, True) is False


def test_unpause_removes_marker(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    set_paused(db, True)
    assert set_paused(db, False) is True
    assert is_paused(db) is False


def test_unpause_when_not_paused_no_change(tmp_path: Path) -> None:
    assert set_paused(tmp_path / "q.db", False) is False


def test_marker_created_in_missing_parent(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "q.db"
    assert set_paused(db, True) is True
    assert is_paused(db) is True
