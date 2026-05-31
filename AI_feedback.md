# AI_feedback

Compact log of AI-assisted changes. Keep entries short; newest first.

## 0.6.0 — storage/schema readiness pass (2026-05-30)

- **Changed**: Consolidated schema into `src/djobs/storage/schema.py` as the
  single runtime authority for both backends (SQLite + PostgreSQL DDL side by
  side; one `JOBS_COLUMN_MIGRATIONS` list). `sqlite.py` / `postgres.py` import
  from it and re-export `SCHEMA_SQL` / `PG_SCHEMA_SQL`.
- **Clarified**: `migrations/*.sql` are historical/manual records, not a runtime
  migration runner. Raw SQL kept intentionally; no ORM/SQLAlchemy.
- **Tests added**: `tests/unit/test_schema.py` (backend drift guard, old-DB
  upgrade, idempotency); `tests/unit/test_concurrency.py` (atomic-claim, no
  double-claim / no loss).
- **Docs**: CHANGELOG `[0.6.0]`, `docs/INTERNALS.md` (Storage Strategy + schema
  authority), `docs/HANDOFF.md` (0.6.0 section).
- **Verification**: `pytest tests/unit`, `ruff check src tests`,
  `mypy src/djobs` — see PR/commit for results.
- **Release readiness**: 0.6.0 ready to tag pending green local checks.
