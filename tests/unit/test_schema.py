"""Tests for the consolidated schema module (djobs.storage.schema).

Locks the contract that:
- both backend schemas declare the same logical columns, and
- the SQLite column-migration path upgrades a pre-existing (old) database.
"""

from __future__ import annotations

import re
import sqlite3

from djobs.storage import schema
from djobs.storage.sqlite import SCHEMA_SQL, SQLiteJobRepository, initialize_schema


def _columns(ddl: str, table: str) -> set[str]:
    """Extract column names from a ``CREATE TABLE <table> ( ... )`` block."""
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\);", ddl, re.DOTALL)
    assert m, f"no CREATE TABLE for {table}"
    cols: set[str] = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("FOREIGN KEY"):
            continue
        cols.add(line.split()[0])
    return cols


def test_sqlite_reexports_authoritative_schema() -> None:
    # The historical name must point at the single source of truth.
    assert SCHEMA_SQL == schema.SQLITE_SCHEMA_SQL


def test_both_backends_declare_same_logical_columns() -> None:
    for table in ("jobs", "job_events", "agents"):
        sqlite_cols = _columns(schema.SQLITE_SCHEMA_SQL, table)
        pg_cols = _columns(schema.POSTGRES_SCHEMA_SQL, table)
        assert sqlite_cols == pg_cols, f"column drift in {table}: {sqlite_cols ^ pg_cols}"


def test_migration_columns_present_in_current_schema() -> None:
    # Every migrated column must already exist in the authoritative DDL so that
    # fresh databases never need the ALTER path.
    jobs_cols = _columns(schema.SQLITE_SCHEMA_SQL, "jobs")
    for column, _definition in schema.JOBS_COLUMN_MIGRATIONS:
        assert column in jobs_cols


def test_column_migration_upgrades_old_database(tmp_path) -> None:
    """A DB created without the newer columns gets them back-filled."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Minimal "old" jobs table missing depends_on_json and resource_key.
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 1,
            run_after TEXT NULL,
            idempotency_key TEXT NULL,
            correlation_id TEXT NOT NULL,
            last_error TEXT NULL,
            leased_by TEXT NULL,
            lease_expires_at TEXT NULL,
            heartbeat_at TEXT NULL,
            started_at TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()

    before = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "depends_on_json" not in before
    assert "resource_key" not in before

    schema.apply_sqlite_column_migrations(conn)
    conn.commit()

    after = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "depends_on_json" in after
    assert "resource_key" in after
    conn.close()


def test_column_migration_is_idempotent(tmp_path) -> None:
    # initialize_schema already adds the columns; re-running must not error.
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    initialize_schema(repo._connection)  # second run, columns already present
    cols = {row["name"] for row in repo._connection.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "depends_on_json" in cols
    assert "resource_key" in cols
