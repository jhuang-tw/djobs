# CLAUDE.md

Project brief for AI agents working ON the djobs codebase. Kept short on
purpose: this file is auto-loaded every session, so it gives orientation and
the must-not-break rules, then points to deeper docs instead of repeating them.

- Full contributor gotchas + reasoning: `.github/skills/djobs-development/SKILL.md`
- Short mirror of the rules for non-Claude agents: `AGENTS.md`
- End-user (runtime) docs: `README.md` and `.github/agents/durable-coder.agent.md`

## What djobs is

djobs is crash-proof task memory for AI coding agents, shipped as an MCP server
plus a thin VS Code sidebar. An agent running a long, multi-file job checkpoints
each unit of work as a durable task in local SQLite; if the IDE crashes or the
chat resets, `resume_session` recovers exactly what is unfinished, so nothing is
lost and finished work is not redone. No broker, no daemon, no cloud - one
SQLite file by default (PostgreSQL optional).

Two artifacts, versioned in lockstep:
- `djobs` Python package (CLI + MCP server) on PyPI.
- `djobs` VS Code extension (read-only task sidebar) on the Marketplace.

## Where things live

- `src/djobs/cli.py` - CLI subcommands: `init`, `install-mcp`,
  `install-instructions`, `doctor`, `serve`, `dashboard`, `status`, `skip`,
  `accept-before`, `archive-workflow`, `explain`, `receipt`, `mcp`, `audit`,
  `pause`, `unpause`.
- `src/djobs/mcp_server.py` - the MCP tools agents call (enqueue/complete/fail,
  resume_session, list_tasks, check_task, audit_log, work_receipt, health,
  multi-agent claim).
- `src/djobs/queue/`, `storage/`, `core/` - queue service, SQLite/Postgres
  repositories, domain models/state machine.
- `vscode-ext/src/` - TypeScript extension (`extension.ts`, `djobsClient.ts`,
  `tasksProvider.ts`, `types.ts`).
- `tests/unit/`, `tests/integration/` - pytest suite.
- `docs/index.html` - GitHub Pages landing page. `migrations/` - historical SQL.

## Critical rules (do not break these)

1. Do NOT commit in this work tree (`c:\src\my\djobs`); the human releases from
   `C:\dev\djobs`. Leave changes uncommitted unless told otherwise.
2. Version lives in ONE place: `src/djobs/__init__.py` `__version__`. After a
   bump run `node vscode-ext/scripts/sync-version.js` so the extension matches.
   `pyproject.toml` is dynamic - never hardcode a version. Never reuse a tagged
   version (v0.7.0-v0.7.3 exist); backward-compatible features = minor bump.
3. Lint has TWO gates: `ruff check` AND `ruff format --check`. Run both.
4. MCP tool output is agent tokens: return compact JSON via `_dumps`; keep
   `_job_to_dict` lean (omits empty fields). In tests assert a field is absent
   or use `.get()`, never `== None`.
5. `QueueService.submit()` auto-generates a UUID `correlation_id` when given
   `None`. `resume_session`/`list_tasks` match path-like correlation_ids
   tolerantly (slash direction, trailing sep, Windows drive-letter case).
6. `doctor` prints ASCII marks `[OK  ]/[INFO]/[FAIL]`; advisory checks are
   `[INFO]`, never `[FAIL]`. A clean `djobs init` shows zero `[FAIL]`.
7. Keep new repo files English and ASCII. Never put CJK or 4-byte emoji through
   `multi_replace_string_in_file` (it corrupts them).
8. On Windows, `python -c "..."` with quotes breaks in PowerShell - write a temp
   `.py` file, run it, delete it.

## Verify before done

```powershell
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe -m ruff format --check src/ tests/
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q          # expect ~348 passed / 18 skipped
Push-Location vscode-ext; npx tsc -p ./ --noEmit; npm run compile; Pop-Location
```

A change is done only when all gates pass, a test covers it, `CHANGELOG.md`
`[Unreleased]` is updated (`[core]` or `[ext]`), and temp scripts are deleted.
Before any upload/release see the pre-upload checklist in the skill.
