# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning policy

This repository ships two independently versioned artifacts:

- **`djobs` Python package** — the durable queue, MCP server, and CLI. It is
  **pre-1.0**: the public API may still change between minor versions.
- **`djobs` VS Code extension** (under `vscode-ext/`) — a thin, read-only
  sidebar over the CLI. Its UI surface is small and stable, so it carries its
  own version line and may sit at a higher number than the Python package.

Entries below are tagged `[core]` or `[ext]` when a change applies to only one
artifact.

## [Unreleased]

### Added
- `[core]` **MCP Registry manifest + `djobs mcp` subcommand.** Added a
  `server.json` so djobs can be published to the official MCP Registry and
  discovered as an MCP server (not only via PyPI / the Marketplace). It launches
  the server with `uvx djobs mcp`, which keeps the verifiable PyPI package
  `djobs` as the registry identifier while running the server through a new
  `djobs mcp` subcommand (equivalent to the `djobs-mcp` console script, honoring
  `DJOBS_DB`). The manifest version is kept in lockstep with `__version__` by
  `sync-version.js`, so the single source of truth is unchanged.
- `[core]` **`djobs explain` — why is each task still here?** A new read-only CLI
  command that explains, in plain language, why every still-visible
  (non-terminal) task has not completed: blocked by dependencies (and flags
  dependencies that can never succeed), scheduled for later, waiting on a
  resource lock, running (or orphaned by a dead worker), failed/dead-lettered,
  or simply pending and possibly stale. Groups output by workflow with a
  category summary, or `--format json` for tools. It mirrors the queue's real
  claim gating, and is the read-only companion to `skip` / `accept-before` /
  `archive-workflow` so users can see and clear work they were "haunted" by.
- `[core]` **`enqueue_task` defaults `correlation_id` to the workspace.** When an
  agent omits `correlation_id`, the task is now grouped under the MCP server's
  working directory (the workspace root) instead of a throwaway UUID, so
  `resume_session` for that workspace recovers it after a crash. Agents can still
  pass an explicit workspace path or session id to override. This makes crash
  recovery work even when the agent forgets to set a correlation id.
- `[ext]` **Native MCP registration — no more hand-editing `.vscode/mcp.json`.**
  On VS Code 1.101+ the extension registers the djobs MCP server programmatically
  (`vscode.lm.registerMcpServerDefinitionProvider`), so a VS Code user gets a
  working agent connection from installing the extension and the runtime alone —
  no JSON to write. The server's `DJOBS_DB` follows the selected queue location,
  so the agent's writes and the sidebar's reads share one database, and it
  re-registers automatically when the runtime, queue location, or interpreter
  changes. It defers to an existing `.vscode/mcp.json` djobs entry to avoid a
  duplicate server, and the `djobs install-mcp` CLI remains for non-VS-Code
  agents (Claude Code, Cursor, Cline) and committed/shared configs. Minimum
  supported VS Code is now 1.101.
- `[ext]` **`uv` support for zero-Python setup.** One-click setup now prefers
  `uv tool install djobs` (after pipx) when `uv` is on PATH. Because `uv` is a
  single binary that can provision its own Python, this is the one install path
  that works with no pre-existing Python on the machine, and updates use
  `uv tool upgrade`. When nothing is found, the setup error now offers "Get uv
  (no Python needed)" alongside "Get Python", matching the `uvx djobs mcp`
  launch used by the MCP Registry entry.

### Changed
- `[core]` **More opinionated agent guidance on evidence and idempotency.** The
  auto-managed guidance block (written to `.github/copilot-instructions.md`) now
  tells agents to give each task a stable `idempotency_key` so resuming after a
  crash re-runs nothing already done, and to always close the loop with
  `complete_task(task_id, evidence="what changed")` / `fail_task` — so a later
  session or human can verify the work and trust `resume_session` instead of
  redoing it. This brings the auto-loaded block in line with the fuller
  `.agent.md` guidance.
- `[docs]` **Repositioned as agent workflow state, not a Python package.** The
  landing page and READMEs now lead with the value (crash-proof, resumable task
  memory for AI coding agents) and an extension-first setup: the VS Code
  extension is the recommended path, `djobs init` is the universal MCP path, and
  installing the Python runtime (`pipx install djobs`) is presented as a managed
  implementation detail rather than the first step.
- `[ext]` **More robust one-click runtime setup.** The extension now picks the
  best available installer (pipx, a project `.venv`, or a Python on PATH —
  including the Windows `py -3` launcher), installs with `pip --user` when not
  using pipx, and pins the concrete interpreter so the sidebar and MCP wiring
  resolve even when the per-user Scripts directory is not on PATH. Setup also
  verifies the install actually launches before reporting success, and when no
  Python runtime exists it says so clearly with a "Get Python" action instead of
  a misleading pip/pipx hint.
- `[ext]` **Sidebar defaults to this workspace.** The VS Code sidebar now shows
  only the current workspace by default, so tasks from another project in the
  shared global queue do not appear as if they belong here. The globe toggle
  still switches to all-workspaces view when you intentionally want the global
  overview. `showCompleted=false` now also hides completed tasks from the tree
  instead of still surfacing completed summaries.
- `[core]` **`djobs status --correlation-id` matches workspace paths
  tolerantly.** The extension's current-workspace view now finds jobs even when
  the stored workflow id and the VS Code workspace path differ only by slash
  direction, trailing separators, or Windows drive-letter case.
- `[core]` **Agent guidance now requires a durable start ritual.** The managed
  instructions tell agents to call `resume_session` before editing and to create
  a durable `enqueue_task` plan before long or multi-step work, so djobs is not
  left as an optional tool the agent can forget during exactly the work that
  needs crash recovery.
- `[ext]` **Start Tracked Workflow command.** The sidebar now has a command and
  empty-state action that copies a prompt telling the agent to resume first,
  enqueue one durable task per meaningful unit, use stable idempotency keys, and
  complete each unit with evidence.
- `[ext]` **Setup recovers from pipx using an old Python.** One-click setup no
  longer stops at the first `pipx install djobs` failure when pipx is backed by
  Python older than djobs supports. It now tries the remaining installers
  (`uv`, workspace `.venv`, Windows `py -3.13/-3.12/-3.11/-3`, then
  `python`/`python3`) and, only if none can satisfy Python 3.11+, shows a short
  recovery message with `uv` / Python 3.11+ actions instead of dumping pip's
  resolver output.

## [0.8.0] - 2026-06-11

### Added
- `[core]` **`djobs init` — one-command onboarding.** Wires `.vscode/mcp.json`,
  installs the agent guidance block, and runs `djobs doctor` in a single step,
  then prints clear next steps. Supports the useful `install-mcp` flags
  (`--full-approve`, `--force`, `--global`, `--db`, `--python`, `--command`,
  `--portable`) plus `--instructions-target {copilot,agent-md,all}`.
- `[core]` **`djobs install-instructions`** writes/updates the managed agent
  guidance block without touching `mcp.json`. Targets: `--target copilot`
  (`.github/copilot-instructions.md`, default), `--target agent-md` (`.agent.md`),
  `--target all` (both); `--print` outputs the block without writing files. The
  block is idempotent — re-running only replaces the djobs-managed section and
  never disturbs your own instructions.
- `[core]` **`resume_session` flags stale and blocked work.** Each incomplete
  task may now carry advisory hints: `stale` / `age_days` when it has been
  unfinished for more than 7 days (so an abandoned workflow reads as "archive
  me" rather than nagging forever), and `blocked_by` listing unfinished
  dependencies. The response adds `stale_count` / `blocked_count` and the
  message points to `djobs archive-workflow` for stale workflows and tells the
  agent to start with the ready (unblocked) tasks.
- `[ext]` **The sidebar flags stale work.** Tasks left incomplete for more than
  7 days now show a `⚠ stale · Nd old` badge with a warning icon, workflow rows
  show a `⚠ N stale` count, and a card points you to archiving an abandoned
  workflow instead of resuming it. Completed tasks are never marked stale. The
  threshold matches `resume_session`'s, so the sidebar and the agent agree.

### Changed
- `[core]` **`djobs doctor`** now reports which instruction file(s) contain the
  guidance block (`.github/copilot-instructions.md` and/or `.agent.md`), and
  points to `djobs install-instructions` when the block is missing. A missing
  block remains non-critical (critical checks stay: package importable and queue
  DB usable).
- `[core]` **Advisory doctor checks no longer look like failures.** Checks that
  are informational (e.g. `djobs-mcp` not on PATH, which still works because
  wiring falls back to the current interpreter) now render as `[INFO]` instead
  of `[FAIL]`, and `doctor --json` tags each check with a `level`
  (`"check"` or `"info"`). A successful `djobs init` no longer shows scary red
  failures.
- `[core]` **Agent tool responses are smaller (token savings).** Every MCP tool
  now returns compact JSON (no pretty-print indentation), and task payloads omit
  empty/irrelevant fields (no `null` `last_error`, `depends_on`, lease columns,
  etc.) instead of always emitting them. `resume_session` and `list_tasks`
  shrink the most. No fields are lost — present values are unchanged.
- `[core]` **`resume_session` returns tasks in insertion order** (instead of an
  arbitrary order) and its message now guides safe continuation: check the
  current file state (e.g. `git diff`) before redoing a task and call
  `complete_task` if it is already done, rather than re-editing it.

### Fixed
- `[core]` **`install-mcp`/`init` no longer mis-wire `DJOBS_DB`.** The `--command`
  flag's dest collided with the subcommand dest, so the database auto-resolver
  could run for `install-mcp`. Renamed the subcommand dest so default
  `install-mcp`/`init` only set a `DJOBS_DB` env when you pass `--db`/`--global`.
- `[core]` **`resume_session` / `list_tasks` no longer miss work due to path
  spelling.** A `correlation_id` that is a filesystem path now matches across
  equivalent spellings — `\` vs `/`, a trailing separator, and Windows
  drive-letter case (`c:` == `C:`) — so a task enqueued as `c:\proj` is still
  found when resuming `C:/proj`. The stored value is never rewritten (matching
  is query-only), so existing data is unaffected; non-path ids (UUIDs, custom
  session ids) behave exactly as before.
- `[core]` **`enqueue_task` returns a helpful error for malformed `payload`.**
  Invalid JSON now yields a clear `{"error": "invalid payload JSON", ...}` with
  a hint and example instead of raising an unhandled exception.

## [0.7.3] - 2026-06-05

### Fixed
- `[ext]` **Pre-0.6 djobs no longer crashes setup with unrecognized flags.** When
  the installed djobs is too old to expose `__version__`, the extension now
  detects it is still present and forces an upgrade prompt before attempting any
  wiring, preventing the `unrecognized arguments: --force --global` error.

## [0.7.2] - 2026-06-05

### Fixed
- `[ext]` **No more dead ends when the MCP launch command is missing.** When
  `.vscode/mcp.json` points the agent at an interpreter that no longer exists
  (e.g. a deleted project `.venv` — the cause of VS Code's *"command … needed to
  run djobs was not found"* error), the setup prompt now installs djobs (if
  missing) **and** re-wires the launch command in one step, instead of leaving
  the broken command in place. A new **"djobs: Set up / Repair djobs"** command
  (Command Palette and sidebar) lets you trigger this manually even after
  dismissing the prompt, and **"Diagnose Setup"** now offers a one-click *Set up
  djobs* button instead of only printing install instructions.

## [0.7.1] - 2026-06-05

### Fixed
- `[ext]` **Auto-update a stale djobs before wiring.** On activation the
  extension now checks the djobs version in the *exact* interpreter it runs
  (the project `.venv`, a configured interpreter, or a global install) and
  offers to update it *before* running any wiring command. This fixes a
  confusing `unrecognized arguments: --global` error that appeared when the
  extension was newer than the installed Python package. Version detection no
  longer depends on the `doctor` command, so it works against older djobs too,
  and the update now upgrades the interpreter the sidebar actually uses.

## [0.7.0] - 2026-06-05

### Added
- `[core]` **`djobs-mcp` console entry point.** After a global install the MCP
  server can be launched directly as `djobs-mcp`, with no per-project Python
  environment required.
- `[core]` **`djobs doctor` command.** Prints a pass/fail checklist of the setup
  (djobs importable, `djobs-mcp` on PATH, queue DB writable, `.vscode/mcp.json`
  wiring, agent guidance block). Use `--json` for machine-readable output.
- `[core]` **`install-mcp` interpreter flags.** New `--python`, `--command`, and
  `--portable` options control how the MCP server is launched in the generated
  `mcp.json`.
- `[ext]` **One-click "Set up djobs"** on activation now installs djobs in an
  isolated way (pipx when available) and wires the agent in a single step, and a
  new **"djobs: Diagnose Setup"** command runs `djobs doctor` in an output panel.
- `[ext]` **Self-healing wiring.** When `.vscode/mcp.json` points the agent at an
  interpreter that no longer exists (e.g. a deleted `.venv`), the extension
  offers to re-wire it to the working djobs install.
- `[ext]` **Update reminder.** The extension auto-updates from the Marketplace,
  but the djobs Python package does not — so when the installed package is older
  than the extension, it now offers a one-click upgrade (`pipx upgrade djobs`).
  `djobs doctor --json` gained a top-level `version` field to support this.

### Changed
- `[core]` **`install-mcp` works in any project — even without a `.venv`.** It
  now wires the agent to the `djobs-mcp` console script when it is on PATH,
  otherwise to the absolute path of the current interpreter (which is guaranteed
  to have djobs), instead of always emitting a relative `${workspaceFolder}/.venv`
  path that breaks in projects with no local virtual environment. Use
  `install-mcp --portable` for the previous relocatable behaviour.
- `[ext]` **The sidebar finds a global djobs install.** Command resolution now
  prefers an explicit interpreter, then a project `.venv`, then the `djobs`
  console script on PATH, so the extension keeps working across projects with no
  Python environment.
- `[core]` **`install.bat user`** installs djobs as a global tool (pipx, or
  `pip --user` fallback) for people who just want to use it; the default
  (no argument) still sets up the full contributor environment.

## [0.6.5] - 2026-05-31

### Fixed
- `[core]` **Source distribution no longer bundles internal files.** The PyPI
  sdist now contains only the package source, database migrations, and the
  standard README/CHANGELOG/LICENSE — previously it also shipped development-only
  files (editor configs, examples, tests, scratch notes).

## [0.6.4] - 2026-05-31

### Changed
- `[core]` **Release notes are now written automatically.** When a `v*` tag is
  pushed, CI summarizes the changes since the previous release into concise,
  user-facing notes and uses them as the GitHub Release body — internal and
  test-only changes are filtered out.

## [0.6.3] - 2026-05-31

### Added
- `[core]` **Fully automated, tag-driven release pipeline.** Pushing a `v*`
  tag now creates the GitHub Release (with notes extracted from this
  changelog), publishes the Python package to PyPI via trusted publishing, and
  publishes the VS Code extension to the Marketplace — all in one workflow,
  with no manual steps.

### Changed
- `[ext]` Marketplace publishing moved into CI (`vsce publish` with a
  `VSCE_PAT` secret), so `core` and `ext` ship together from a single tag.

## [0.6.2] - 2026-05-31

### Added
- **`install.bat`** — one-click Windows dev setup: creates `.venv`, installs
  all dependencies, and enables the pre-push gate.
- **`pre-commit` gate** — replaced the custom shell hook with the
  industry-standard `pre-commit` framework. The `ruff` version is pinned, so
  lint results never drift between machines or between local and CI.
- `[core]` Python 3.14 added to the CI test matrix and package classifiers.

### Changed
- CI lint job now runs the same pinned `ruff` version as the local hook, driven
  by `.pre-commit-config.yaml` as the single source of truth.

### Fixed
- Postgres integration tests now probe the connection once at module level
  (`connect_timeout=5`), cutting skip latency from ~20 s to under 1 s when
  Postgres is unavailable.

## [0.6.1] - 2026-05-30

### Changed
- `[core]` CLI agent-guidance improvements and `depends_on` serialization so
  task dependencies round-trip cleanly through the CLI and sidebar.
- `[ext]` Sidebar refinements, including `depends_on` display.

## [0.6.0] - 2026-05-30

### Changed
- `[core]` **Schema authority consolidated** into a single module
  `src/djobs/storage/schema.py`. It is now the runtime source of truth for both
  backends: `SQLITE_SCHEMA_SQL` and `POSTGRES_SCHEMA_SQL` live side by side, and
  the post-initial column upgrades are driven by one `JOBS_COLUMN_MIGRATIONS`
  list via `apply_sqlite_column_migrations` / `apply_postgres_column_migrations`.
  `sqlite.py` and `postgres.py` no longer embed their own DDL; they import from
  `schema.py` and re-export the historical names `SCHEMA_SQL` / `PG_SCHEMA_SQL`
  for backward compatibility.
- `[core]` Clarified that `migrations/*.sql` are a **historical / manual**
  migration record for operators applying changes to a pre-existing database by
  hand — they are **not** executed by a runtime migration runner. Fresh
  databases are created from `schema.py`.

### Added
- `[core]` **Backend schema drift guard** — `tests/unit/test_schema.py` asserts
  that the SQLite and PostgreSQL schemas declare the same logical columns, that
  every migrated column already exists in the current schema, and that the
  SQLite column-migration path upgrades a pre-existing database idempotently.
- `[core]` **Atomic-claim concurrency tests** — `tests/unit/test_concurrency.py`
  proves no task is ever double-claimed or lost under contention, both with a
  shared repository (single connection + lock) and with separate connections
  (exercising the database-level `BEGIN IMMEDIATE` write lock).

### Notes
- Raw SQL remains the intentional storage strategy for queue correctness; no
  ORM / SQLAlchemy is introduced. SQLite is the first-class default, with the
  PostgreSQL backend optional via `pip install "djobs[pg]"`.

## [0.5.0] - 2026-05-29

### Added
- `[core]` **Multi-agent coordination** — several agents can safely share one
  queue:
  - `claim_task` / `heartbeat_task` / `release_task` MCP tools with atomic
    leases (SQLite `BEGIN IMMEDIATE`, PostgreSQL `FOR UPDATE SKIP LOCKED`).
  - Task dependencies via `enqueue_task(depends_on=...)` — a task is not
    claimable until all of its dependencies have succeeded.
  - Resource locks via `enqueue_task(resource_key=...)` — at most one task per
    key runs at a time.
  - Agent registry: `register_agent`, `agent_heartbeat`, `list_agents`, with
    automatic OFFLINE reaping of agents that stop heartbeating.
- `[core]` **Read-only web dashboard** — `djobs dashboard` serves a
  cross-agent view of queue health, tasks, and the live agent fleet at
  `http://127.0.0.1:8787` (stdlib HTTP server, no extra dependencies).
- `[core]` CI now runs the repository contract tests against a real PostgreSQL
  service container; `DJOBS_REQUIRE_PG=1` turns a missing database into a hard
  failure instead of a silent skip.
- `[ext]` Sidebar falls back to a human-readable payload field
  (`summary` / `title` / `name`) instead of showing a raw task id.
- `[core]` Job payloads are now size-limited (default 256 KiB) to protect the
  database and claim scans from oversized blobs. Configure via the
  `DJOBS_MAX_PAYLOAD_BYTES` environment variable or the `max_payload_bytes`
  argument to `QueueService`; set to `0` to disable. Oversized payloads raise
  `PayloadTooLargeError`.
- Static type checking with `mypy` is now part of CI (new `typecheck` job) and
  the `dev` extra. `src/djobs` is type-clean under `mypy --ignore-missing-imports`.

### Changed
- `[core]` MCP server now exposes **14 tools** (8 core + 6 multi-agent).
- `[core]` `succeeded → archived` is now a valid state transition, so completed
  workflows can be archived for cleanup.
- `[ext]` Sidebar labels use neutral "task(s)" wording instead of "file(s)".
- `[core]` `SchedulerLoop.tick()` now proactively reaps stale agents every
  cycle (previously they were only marked OFFLINE lazily when `list_agents` or
  the dashboard read them). `TickResult` gained a `reaped` counter.
- `[core]` Optimized the job-claim path in both backends: running-per-type
  counts and held `resource_key`s are now computed once per claim instead of
  once per candidate row (eliminating an N+1 query pattern), and candidate rows
  are scanned lazily so the claim stops at the first ready task.

### Security
- `[core]` The dashboard logs a warning when bound to a non-loopback address,
  reminding operators that it has no authentication and should only be exposed
  on a trusted network. CLI `--host` help documents the same caveat.

### Fixed
- `[core]` Documentation drift across `docs/` (tool count, test count,
  `depends_on` / `resource_key`, agent registry, and dashboard sections).

## [0.3.0] and earlier

Foundational releases, developed in phases:

- Durable SQLite job queue with a state machine (pending → running →
  succeeded / failed), retry with exponential backoff, and a dead-letter queue.
- Lease-based claiming and expired-lease recovery; `SchedulerLoop` and
  `WorkerPool` with graceful drain.
- Observability: structured logging, metrics, job inspection, and an event log
  that powers the `audit_log` MCP tool.
- PostgreSQL backend behind the same `JobRepository` protocol, plus a shared
  repository contract test suite.
- MCP server (stdio) with crash-recovery tools (`enqueue_task`, `complete_task`,
  `fail_task`, `check_task`, `list_tasks`, `resume_session`, `audit_log`,
  `health`) and the `djobs` CLI.

[Unreleased]: https://github.com/jhuang-tw/djobs/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/jhuang-tw/djobs/releases/tag/v0.5.0
