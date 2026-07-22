# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning policy

The Python package, VS Code extension, and MCP Registry manifest are released in
lockstep from the version in `src/djobs/__init__.py`. The project is pre-1.0, so
public interfaces may still change between minor versions. Entries below use
`[core]`, `[ext]`, `[docs]`, or `[release]` to identify the affected surface.

## [Unreleased]

## [0.13.0] - 2026-07-22

### Changed
- `[core]` **Six-tool coding MCP.** The default `djobs-mcp`, `djobs mcp`, VS Code native registration, generated `.vscode/mcp.json`, and MCP Registry package now launch a dedicated coding server exposing only `resume_delta`, `enqueue_batch`, `complete_batch`, `check_task`, `fail_task`, and `work_receipt`.
- `[core]` **Full queue is explicit.** The prior complete MCP surface remains available through `djobs-mcp-full` or `python -m djobs.delta_mcp` for users who intentionally need claims, leases, fleet registration, health, audit, and legacy single-task tools.

### Performance
- `[core]` **Lower fixed tool-schema context.** Multi-agent and administrative tool definitions no longer consume context in every ordinary coding session; permanent registry tests guard the exact default surface.

## [0.12.1] - 2026-07-22

### Changed
- `[core]` **Passive coding MCP.** The normal, low-token, and delta-context MCP entry points now initialize only the durable queue and stdio server. They no longer start a worker pool or scheduler behind the coding agent.

### Removed
- `[core]` **Implicit background polling.** Removed the embedded daemon thread, its two-second worker polling, five-second scheduler polling, built-in handler registration, and process-exit thread cleanup from MCP startup.

### Compatibility
- `[core]` The standalone `djobs serve` command, `Daemon`, `WorkerPool`, and handler APIs remain available for users who explicitly need general-purpose job execution; only automatic startup inside coding-agent MCP processes was removed.
- `[core]` **Python 3.10 support.** Lowered the runtime floor from Python 3.11 to 3.10, replaced 3.11-only `datetime.UTC` usage with `timezone.utc`, and added Python 3.10 to the tested CI matrix.

## [0.12.0] - 2026-07-22

### Changed
- `[ext]` **Headless coding integration.** The VS Code extension is now a thin setup, native-MCP, hook, pause/resume, and diagnostics layer. It no longer creates an Activity Bar container, task tree, status badge, prompt-action UI, or task-management context menus.
- `[ext]` **Coding-first setup.** Setup now installs deterministic smart-mode hooks directly, so the extension's runtime work is focused on preventing repeated tests, builds, linters, type checks, and context reconstruction.

### Removed
- `[ext]` **Background task polling.** Removed the five-second Python status poller and overlapping-refresh guard because no persistent task view remains.
- `[ext]` **Sidebar implementation.** Removed the 600-line tree provider and the client/status types used only to render, inspect, archive, delete, or prompt from the sidebar.
- `[ext]` **Redundant update/network logic.** Removed custom Marketplace/PyPI update checks; VS Code handles extension updates, while the explicit setup command keeps the Python engine aligned.

## [0.11.0] - 2026-07-22

### Added
- `[core]` **Deterministic command checkpoints.** `djobs init` now installs compatible `preToolUse` and `sessionStart` hooks that checkpoint meaningful Bash and PowerShell commands before execution, preserve output and exit status, and restore failed or interrupted work without relying on the model to remember an MCP call.
- `[core]` **Explainable savings analytics.** Added `djobs gain` with `stats` and `state` aliases, 24-hour, 30-day, and all-time views, source breakdowns, daily history, an ASCII graph, recent records, and JSON export. Values are explicitly labeled estimates rather than provider billing data.

### Changed
- `[core]` **Cleaner checkpoint lifecycle.** Successful automatic checkpoints are archived after evidence is recorded, while failed and interrupted checkpoints remain visible and recoverable. Hook processing remains fail-open, and custom or global database paths are shared with MCP configuration.
- `[docs]` **One maintained documentation system.** Rewrote the README, contributor guide, AI contributor rules, release runbook, Marketplace page, package metadata, MCP manifest, and public website around one product description and a conservative compatibility matrix.
- `[ext]` **Marketplace discoverability.** Renamed the extension surface to Agent Checkpoints, added supported metadata, categories, and search terms, and aligned the extension README with automatic hooks and `djobs gain`.

### Removed
- `[docs]` **Stale duplicated documentation and scratch tooling.** Removed phase roadmaps, AI handoff snapshots, obsolete architecture and implementation notes, duplicated Durable Coder prompts, machine-specific contributor skills, accidental diff files, and outdated packaging/release scripts that contradicted the current code or release workflow.

## [0.10.0] - 2026-07-21

### Added
- `[core]` **Revision-based delta recovery.** Added `resume_delta`, which returns only workflow changes since the caller's last revision instead of replaying the full unfinished-task set. Responses remain bounded by an explicit token budget and include deterministic workspace state hashes.
- `[core]` **Monotonic context revision ledger.** Added an append-only SQLite revision ledger with per-event task snapshots, automatic backfill for existing databases, and deletion tombstones so cursors never move backward and permanently deleted tasks remain observable to resuming agents.

### Changed
- `[core]` **Delta context is available from every default MCP entry point.** Both `djobs-mcp` and `djobs mcp` now expose `resume_delta` alongside the existing batch and capsule tools, while the legacy server entry point remains available for compatibility.
- `[docs]` **Context-efficient workflow guidance.** Expanded the context-efficiency documentation with revision persistence, pagination, state-hash validation, and recovery examples for local and hosted coding agents.

### Fixed
- `[core]` **Delta pagination preserves historical ordering.** Each revision stores the task state at that event, preventing a paginated response from revealing a later status before the corresponding event is delivered.
- `[core]` **Tiny budgets and concurrent reads remain consistent.** Exhausted responses do not advance the cursor, and revision, task state, and `state_hash` are read from one SQLite snapshot.

## [0.9.1] - 2026-07-19

### Added
- `[core]` **Batch checkpoint tools.** Added `enqueue_batch` and `complete_batch` so agents can create or close many durable tasks in one MCP call instead of paying one orchestration round trip per task.
- `[core]` **Budgeted resume capsules.** Added `resume_capsule`, a compact and paginated recovery view that preserves exact queue state in SQLite while exposing only the fields and number of tasks that fit the requested context budget.

### Changed
- `[core]` **Context-efficient MCP is now the default.** The installed `djobs-mcp` command, `djobs mcp`, and the checked-in VS Code MCP configuration now expose batch and capsule tools without requiring a separate opt-in entry point. The legacy `python -m djobs.mcp_server` entry point remains available.
- `[core]` **Native batch arrays.** Batch tools now accept native arrays directly while keeping JSON-string compatibility, removing redundant model-side serialization.
- `[docs]` **Accurate product positioning.** Package metadata and context-efficiency documentation now describe measured, budgeted orchestration behavior instead of making an unqualified token-savings claim.
- `[release]` **MCP SDK compatibility boundary.** The Python dependency is constrained to `mcp>=1.0,<2` until the v2 API is explicitly validated.

### Fixed
- `[core]` **Tiny context budgets are respected.** `resume_capsule` no longer forces an oversized first task into the response; it returns an empty page with `budget.exhausted=true` so callers can increase the budget or retrieve a full record intentionally.
- `[core]` **CLI database overrides remain intact.** Routing `djobs mcp` through the context-efficient server preserves the existing `--db` behavior and leaves every non-MCP CLI command unchanged.

## [0.9.0] - 2026-06-14

### Added
- `[core]` **AI Work Receipt.** New `djobs receipt` command and `work_receipt`
  MCP tool produce an evidence-backed summary of what the agent actually did:
  totals (completed / remaining / failed / archived), the files changed, the
  evidence recorded on each completed task, evidence coverage, and a recommended
  next step. It is read-only (works even while paused) so a human or the next
  agent can trust and continue the work without re-reading the whole chat.
- `[core]` **Git-aware evidence.** When run inside a git working tree, the Work
  Receipt now folds in ground truth from git: it reports how many files git sees
  as actually changed (with a `git diff --shortstat` summary) and flags any file
  a task *claimed* to change that git shows no pending change for — labeled
  honestly as "may already be committed, or was not actually modified". This lets
  a human cross-check the agent's claims against what the repository really shows.
  The inspection is read-only and never raises; pass `--no-git` to skip it.
- `[core]` **Pause switch.** New `djobs pause` / `djobs unpause` commands (and a
  **Pause djobs** / **Resume djobs** toolbar button in the VS Code sidebar)
  temporarily stop agents from resuming or enqueueing durable work. While paused,
  `resume_session` and `enqueue_task` return a clear "paused, work normally"
  notice instead of surfacing tasks, so a workflow that wedges on a hanging
  command can no longer trap the agent in a resume loop. Pausing deletes nothing
  and is fully reversible; `djobs status` reports the paused state.

### Fixed
- `[core]` **Work Receipt avoids false git mismatch warnings.** If git is a
  working tree but `git status --porcelain` cannot produce a reliable file list
  (for example because git is unavailable, times out, or the index is corrupt),
  the receipt now reports the git check as unavailable instead of treating every
  task-claimed file as "not in the working tree".
- `[core]` **Managed guidance no longer hijacks the user's prompt.** The
  auto-installed agent guidance block (and the MCP server instructions) used to
  tell agents to treat ordinary requests like "continue", "fix this", or "run
  tests" as a trigger to call `resume_session` and enqueue durable plans — in
  every workspace where djobs was wired, including unrelated projects. The block
  is now an explicitly optional tool that must never reinterpret the user's
  request, never starts a session by calling djobs, and instructs agents to
  treat tool output as data, not commands. Re-run `djobs install-instructions`
  in a project to update an already-installed block.
- `[ext]` **Pause button handles stale Python packages.** If the sidebar's Pause
  / Resume command is available but the installed `djobs` Python package is too
  old to expose `djobs pause` / `djobs unpause`, the extension now offers to
  update the package and retries the action instead of surfacing argparse's
  `invalid choice: 'pause'` error.
- `[ext]` **Sidebar shows switch state.** The task view now includes explicit
  `djobs: Active/Paused` and `Prompt actions: On/Off` rows, so toolbar toggles
  are understandable even though VS Code view-title buttons are not real
  checkboxes.

## [0.8.6] - 2026-06-13

### Added
- `[ext]` **Visible prompt-action toggle.** Added explicit **Enable Prompt
  Actions** and **Disable Prompt Actions** commands in the sidebar toolbar and
  Command Palette, so users can discover the opt-in prompt workflow without
  digging through settings. Prompt actions still default to off, and djobs never
  asks on startup whether to enable them.

## [0.8.5] - 2026-06-13

### Removed
- `[ext]` **Removed prompt-driving extension flows.** The VS Code extension no
  longer generates, copies, or opens Chat prompts. The Start/Resume workflow
  commands and auto-takeover settings were removed; the extension now focuses
  on setup, MCP registration, diagnostics, the sidebar, skip/archive, and task
  inspection.

### Added
- `[ext]` **Guard against prompt-driving regressions.** Release guard tests now
  fail if the extension manifest or TypeScript source reintroduces Chat-opening
  prompt commands, auto-takeover settings, or prompt-copy helpers.
- `[ext]` **Marketplace update reminder.** The extension now checks the VS Code
  Marketplace at most once per day and notifies the user when a newer djobs
  extension version exists, opening the normal Marketplace / Extensions update
  surfaces instead of trying to drive Chat or an agent prompt.
- `[ext]` **Practical sidebar task controls.** Each task now has right-click
  actions to archive it, permanently delete it, inspect its JSON, or view its
  audit history. Archive keeps history; delete removes the task and its events.
- `[ext]` **Opt-in prompt actions.** Users who explicitly enable
  `djobs.promptActions.enabled` get a manual **Prompt Agent to Finish Workflow**
  action. It is off by default, and djobs never asks on startup whether to
  enable it.

## [0.8.4] - 2026-06-12

### Added
- `[ext]` **Start Tracked Workflow is copy-first.** The sidebar Start action now
  copies the tracking prompt and offers an explicit Open Chat action instead of
  immediately opening Chat, so preparing the workflow never spends tokens by
  itself.
- `[docs]` **Vendor-neutral token-saving positioning.** Public metadata, README,
  and the landing page now lead with djobs as token-saving durable context for
  Codex, Claude, Gemini, Copilot, Cursor, Cline, and any MCP-compatible coding
  agent, instead of reading as a VS Code/Copilot-specific helper.
- `[docs]` **Compatibility matrix and tested-on disclaimer.** Public docs now
  state that the current implementation is developed and tested with GitHub
  Copilot in VS Code, while Claude Code, Cursor, Cline, Codex, and Gemini are
  intended through MCP-capable hosts but still need broader end-to-end testing.
- `[docs]` **Search indexing support for GitHub Pages.** Added `robots.txt` and
  `sitemap.xml`, and updated the Pages workflow to publish them alongside the
  landing page and demo SVG. This gives Google Search Console a concrete sitemap
  URL and makes the new site easier to crawl.
- `[release]` **CI guards for publishable surfaces.** Added pytest checks that
  fail CI if Pages stops publishing `robots.txt` / `sitemap.xml`, if the release
  workflow falls back to generated notes instead of `CHANGELOG.md`, or if the
  current package version is missing a dated changelog section.

## [0.8.3] - 2026-06-12

### Changed
- `[ext]` **Auto-takeover now uses one-time authorization.** The first prompt asks
  whether djobs may take over future AI work in the workspace. Choosing **Allow
  auto takeover** only changes the workspace setting; it does not open Chat or
  spend tokens immediately. Users can choose `askOnce`, `openChat`, `prompt`, or
  `off`.
- `[core]` **Natural work requests trigger djobs guidance.** The managed agent
  guidance now treats ordinary requests like "continue", "fix this", "run
  tests", or "release" as signals to call `resume_session` first and enqueue a
  durable plan before multi-step edits, without requiring users to mention djobs.

## [0.8.2] - 2026-06-12

### Added
- `[ext]` **Auto-takeover prompt for agent work.** When djobs is installed and
  ready, the VS Code extension can proactively offer to resume unfinished tasks
  or start a tracked workflow for the current workspace. The Start Tracked
  Workflow command opens Chat directly and copies the prompt, reducing the
  manual copy/open loop.
- `[core]` **`djobs token-savings` experiment.** Added a CLI estimator that reads
  completed tasks and evidence from the queue and estimates replay/re-plan
  tokens avoided by durable task state. It prints explicit assumptions and JSON
  output for promotion-friendly measurements.

### Changed
- `[docs]` **Trigger mechanics are explicit.** README and the extension README
  explain what djobs can and cannot automatically intercept: MCP tools and
  guidance are wired automatically, while the extension brings Chat to the
  correct resume/enqueue prompt before multi-step edits.
- `[core]` **Faster, more consistent internals.** `resume_session` / `list_tasks`
  hydrate matching tasks in a single SQLite query instead of one query per task,
  and `correlation_id` normalization plus the stale-after threshold live in
  shared `djobs.core` modules so CLI, MCP server, and extension cannot drift.
- `[ext]` **Steadier sidebar refresh.** Concurrent refreshes are coalesced so the
  auto-refresh timer and config changes cannot stack overlapping Python
  processes, and auto-takeover prompts are suppressed when the status snapshot is
  unavailable or the agent is not wired to the queue.

## [0.8.1] - 2026-06-11

### Fixed
- `[release]` **Release process is now repeatable.** Added `docs/RELEASE.md` as
  the release runbook, changed the GitHub Release workflow to copy release notes
  from the matching `CHANGELOG.md` version section instead of generating them
  from commit logs, and made duplicate VS Code Marketplace publishes non-fatal
  when a rerun sees that the version already exists. This prevents future large
  releases from collapsing into misleading autogenerated notes or red reruns.

## [0.8.0] - 2026-06-11

### Added
- `[core]` **MCP Registry manifest + `djobs mcp` subcommand.** Added `server.json`
  so djobs can be published to the official MCP Registry and discovered as an
  MCP server. It launches with `uvx djobs mcp` and keeps the PyPI package
  `djobs` as the verifiable registry identifier.
- `[core]` **`djobs explain` — why is each task still here?** Added a read-only
  CLI command that explains why visible tasks remain: blocked, scheduled,
  resource-waiting, running, failed, dead-lettered, stale, or ready.
- `[core]` **`enqueue_task` defaults `correlation_id` to the workspace.** Tasks
  submitted without an explicit correlation id now group under the MCP server's
  working directory so `resume_session` can find them after crashes.
- `[core]` **`djobs init` and `djobs install-instructions`.** Added one-command
  onboarding and an idempotent way to install/update the managed agent guidance
  block without touching MCP config.
- `[core]` **`resume_session` flags stale and blocked work.** Resume output now
  includes advisory stale/blocked hints so agents can start with runnable tasks
  and archive abandoned workflows.
- `[ext]` **Native MCP registration.** On VS Code 1.101+, the extension registers
  the djobs MCP server programmatically and keeps the agent DB aligned with the
  sidebar DB.
- `[ext]` **`uv` support for zero-Python setup.** One-click setup can use
  `uv tool install djobs`, and setup errors point users to uv when no Python is
  available.
- `[ext]` **Sidebar workflow controls.** Added stale/blocked visualization,
  current-workspace default scope, true `showCompleted=false` filtering, Start
  Tracked Workflow, skip, archive, and resume helpers.

### Changed
- `[core]` **More opinionated agent guidance.** Managed instructions now push
  agents to resume first, enqueue durable plans before long work, use stable
  idempotency keys, and complete tasks with evidence.
- `[core]` **Agent tool responses are smaller.** MCP tools return compact JSON
  and omit empty fields to reduce token overhead.
- `[core]` **`resume_session` returns tasks in insertion order** and tells agents
  to inspect current file state before redoing work.
- `[docs]` **Repositioned as agent workflow state.** README, extension README,
  and landing page lead with crash-proof workflow state instead of Python package
  mechanics.
- `[ext]` **More robust one-click setup.** Setup tries pipx, uv, `.venv`, Windows
  `py -3.13/-3.12/-3.11/-3`, and `python`/`python3`, pins concrete interpreters,
  and gives concise Python-too-old recovery messages.

### Fixed
- `[core]` **`install-mcp` / `init` no longer mis-wire `DJOBS_DB`.** The subparser
  destination no longer collides with `--command`.
- `[core]` **Path-like `correlation_id` matching is tolerant.** Resume/list/status
  matching tolerates slash direction, trailing separators, and Windows
  drive-letter case.
- `[core]` **Malformed `enqueue_task` payloads return helpful errors** instead of
  unhandled JSON exceptions.
- `[ext]` **Setup no longer dies on stale or unsupported runtimes.** Dead MCP
  interpreters, old djobs installs, and pipx backed by old Python now get repair
  paths instead of raw failures.
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

[Unreleased]: https://github.com/jhuang-tw/djobs/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/jhuang-tw/djobs/releases/tag/v0.10.0
[0.5.0]: https://github.com/jhuang-tw/djobs/releases/tag/v0.5.0






