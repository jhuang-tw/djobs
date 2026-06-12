---
name: djobs-development
description: >-
  Verified gotchas and workflow for working ON the djobs codebase (the Python
  package in src/djobs and the VS Code extension in vscode-ext/). USE THIS when
  editing, testing, refactoring, releasing, or reviewing djobs itself - it
  captures mistakes that have actually bitten contributors so you don't repeat
  them. NOT for end users of djobs (they should read the README / Durable Coder
  agent instead).
---

# Working on the djobs codebase

This skill is the memory of mistakes that have actually cost time in this repo.
Read the relevant section before the matching task; follow the verification
steps at the end before claiming done.

## Repo shape (orient first)

- `src/djobs/` - the Python package: `cli.py` (subcommands), `mcp_server.py`
  (the 14 MCP tools agents call), `queue/service.py`, `storage/`, `core/`.
- `vscode-ext/` - the TypeScript VS Code extension (thin read-only sidebar over
  the CLI). Build with `npm run compile`; type-check with `npx tsc -p ./ --noEmit`.
- `tests/unit/` + `tests/integration/` - pytest. Unit tests use `tmp_path` +
  `monkeypatch.chdir` + the `DJOBS_DB` env var; never touch a real workspace.

## Versioning - single source, lockstep (easy to get wrong)

- The version lives in **exactly one place**: `src/djobs/__init__.py`
  `__version__`. `pyproject.toml` is `dynamic = ["version"]` (hatchling reads
  `__init__.py`) - do NOT hardcode a version there.
- The VS Code extension version is kept in lockstep: after bumping
  `__init__.py`, run `node vscode-ext/scripts/sync-version.js` (or
  `npm run compile`, which calls it) so `vscode-ext/package.json` matches.
- `tests/unit/test_imports.py` asserts the version *shape* (regex
  `\d+\.\d+\.\d+`), not a literal - so a bump does not require editing a test.
- Never reuse a version that already has a git tag (e.g. v0.7.0-v0.7.3 exist):
  PyPI and the Marketplace reject a same/older number, so the fix silently never
  ships. New features that are backward compatible = minor bump (0.7.x -> 0.8.0).

## Two-machine workflow (do NOT commit here)

- `c:\src\my\djobs` is the **work** machine (has the `.venv`, Node tools, where
  edits happen). It does NOT push.
- `C:\dev\djobs` is the **release** machine (git push, PyPI, Marketplace).
- Make changes on the work machine and leave them uncommitted; the human syncs
  and commits/pushes from the release machine. Do not run `git commit`/`git push`
  here unless explicitly told to.

## PowerShell on Windows (recurring foot-guns)

- `python -c "..."` with escaped quotes **breaks** in PowerShell (mangled
  quoting). Heredocs (`python - <<'PY'`) also fail and can leave the terminal
  stuck in a `>>>` REPL so every later command errors. Write a small temp
  `.py` file, run it, delete it.
- `ruff check` passing is **not** enough: CI also runs `ruff format --check`.
  Always run BOTH before claiming green. Apply with `ruff format src/ tests/`.
- Chain gated steps with `&&` (stop on failure), never `;` (continues on error
  and can deploy broken code).

## Editing files with CJK / emoji (data-loss risk)

- `multi_replace_string_in_file` can corrupt rare CJK characters and 4-byte
  emoji on write. For any edit containing CJK, use a single
  `replace_string_in_file` one at a time, and grep afterwards for mojibake.
- Do not put 4-byte emoji in a replacement `newString` in `.md` files - they
  turn into the U+FFFD replacement character. Use ASCII tags instead. (The djobs
  repo is English-only, which sidesteps this - keep new repo files English.)

## argparse: flag dest vs subparser dest collision (real bug fixed here)

- `--command` had `dest="command"`, which collided with
  `add_subparsers(dest="command")`. Result: for `install-mcp`/`init`,
  `args.command` got overwritten by the flag default (None), so the
  db-auto-resolver ran and wrote a stray `DJOBS_DB` env. Fixed by renaming the
  subparser dest to `subcommand`. When adding flags, make sure a flag `dest`
  never shadows the subparser `dest`.

## MCP tool semantics (src/djobs/mcp_server.py)

- Every tool return is **tokens the agent must read**. Return compact JSON via
  the `_dumps()` helper (`separators=(",", ":")`), never `indent=2`.
- `_job_to_dict()` is **lean**: it omits empty fields (`last_error`,
  `depends_on`, lease columns, ...). So in tests do NOT assert a field equals
  `None` - assert it is absent, or use `.get()`. (An old `leased_by is None`
  assertion broke when serialization went lean.)
- `QueueService.submit()` **auto-generates a UUID `correlation_id`** when you
  pass `None`. So a task is never "without" a correlation_id; do not assume the
  field is omitted.
- `resume_session` / `list_tasks` match `correlation_id` **tolerantly** for
  path-like ids (`\` vs `/`, trailing sep, Windows drive-letter case) via
  `_correlation_id_variants()` in `mcp_server.py`. The CLI `status
  --correlation-id` command uses the same logic (a sibling helper in `cli.py`)
  so the extension's current-workspace view finds tasks across spelling
  variants. Matching is query-only; the stored value is never rewritten
  (back-compatible with existing rows).
- `resume_session` also annotates tasks with advisory `stale` / `age_days`
  (>`_STALE_AFTER_DAYS`, currently 7) and `blocked_by`. Keep the extension's
  `STALE_AFTER_DAYS` in `vscode-ext/src/tasksProvider.ts` in sync with it.
- The managed instructions are deliberately stricter than a generic tip:
  coding sessions start with `resume_session`, and long/multi-step work must
  enqueue a durable plan before edits. Keep tests in
  `tests/unit/test_install_instructions.py` aligned when changing this block.
- Extension setup must not assume `pipx` implies a compatible Python. pipx may
  be installed with Python <3.11; `installPackage()` therefore tries all
  installer candidates and collapses Requires-Python failures into a concise
  uv / Python 3.11+ recovery message.

## doctor output (src/djobs/cli.py)

- Use ASCII status marks only (`[OK  ]`, `[FAIL]`, `[INFO]`) - no unicode marks in
  stdout (Windows cp1252 can raise on encode).
- Informational checks (e.g. `djobs-mcp` not on PATH, which still works because
  wiring falls back to the current interpreter) must render as `[INFO]`, not
  `[FAIL]`, and carry `level: "info"` in `--json`. A successful `djobs init`
  must show zero `[FAIL]` lines, or users stop trusting the tool.
- Critical = djobs importable AND queue DB usable. Only those exit non-zero.

## ordering / time bugs

- For "all earlier tasks" logic use SQLite `rowid` (monotonic insertion order),
  NOT `created_at`: on Windows the clock can tick ~15ms so sibling rows created
  in the same tick collide and get missed. (`accept-before` hit this.)

## Tests and quality gates (what CI runs == what you run)

CI is the contract. `.github/workflows/ci.yml` runs four jobs on every push and
PR to `main`; your local gate must reproduce all four before you say "done".
The single source of truth for lint/type/test is `.pre-commit-config.yaml`
(shared by the local pre-push hook AND CI), so local and CI cannot drift.

1. `lint` (Python 3.13): `ruff check` then `ruff format --check`. CI runs these
   via pinned pre-commit (`pre-commit run ruff-check --all-files` then
   `ruff-format`). They are TWO separate gates - passing one is not enough.
2. `typecheck` (Python 3.13): `pip install -e ".[dev,pg]"` then `mypy`.
   mypy is configured (`pyproject.toml [tool.mypy]`) as `files = ["src/djobs"]`,
   `ignore_missing_imports = true`, `warn_unused_ignores`, `warn_redundant_casts`.
   psycopg MUST be installed for a clean run, otherwise `psycopg.Connection`
   attributes report false errors; the `pg` extra covers this.
3. `test` (matrix Python 3.11, 3.12, 3.13, 3.14): `pip install -e ".[dev]"`
   then `pytest -q --tb=short`. SQLite path only; no Postgres here.
4. `test-postgres` (Python 3.13 + a `postgres:16` service): installs
   `".[dev,pg]"`, sets `DJOBS_TEST_PG_DSN=postgresql://djobs:djobs@localhost:5432/djobs`
   and `DJOBS_REQUIRE_PG=1`, and runs only
   `tests/integration/test_repository_contract.py` against real Postgres.

### Local one-time setup (per clone)

```powershell
pip install -e ".[dev,pg]"   # ruff (via pre-commit), mypy, pytest, pre-commit, psycopg
pre-commit install           # wires the pre-push hook (runs the gates on git push)
# If a prior shell hook is set: git config --unset core.hooksPath  (then re-run install)
```

### The full local gate (run before declaring done)

```powershell
# Python - all four must pass, in this order
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe -m ruff format --check src/ tests/    # SEPARATE gate from ruff check
.\.venv\Scripts\python.exe -m mypy                               # needs psycopg installed
.\.venv\Scripts\python.exe -m pytest -q                          # expect ~348 passed / 18 skipped

# Or reproduce CI's exact lint/type/test in one shot:
.\.venv\Scripts\python.exe -m pre_commit run --all-files

# Extension (no JS test framework; type-check + compile is the gate)
Push-Location vscode-ext; npx tsc -p ./ --noEmit; npm run compile; Pop-Location
```

- Apply fixes with `ruff format src/ tests/` and `ruff check --fix src/ tests/`.
- `pre-commit run <id>` takes ONE id; CI runs `ruff-check` and `ruff-format` as
  two steps. The ruff version is pinned (`rev: v0.15.14`) in an isolated
  environment, so it never drifts from whatever ruff is in your venv.
- `pytest` config (`pyproject.toml`): `testpaths = ["tests"]`,
  `pythonpath = ["src"]` (so imports work even without setting `PYTHONPATH`).

## Ruff rule set (so edits pass first try)

`target-version = py311`, `line-length = 99`, selected rules:
`E, W` (pycodestyle), `F` (pyflakes), `I` (isort), `N` (pep8-naming),
`UP` (pyupgrade), `B` (bugbear), `SIM` (simplify), `RUF` (ruff). Notes:

- `F841` fails on assigned-but-unused locals - do not bind a result you don't
  use (e.g. `dependent = enqueue_task(...)` when you never read `dependent`).
- `RUF100` fails on an unused `# noqa` - only add a `noqa` for a code that is
  actually selected. `BLE` (blind-except) is NOT selected, so a plain
  `except Exception as exc:` is fine and needs no `noqa`.
- These are 3rd-party-style imports inside functions in `cli.py`/`mcp_server.py`
  by design (lazy import for fast CLI startup); keep that pattern.

## Test conventions (match the existing suite)

- Isolation: use `tmp_path` + `monkeypatch.chdir(tmp_path)` and point the queue
  at a temp DB with `monkeypatch.setenv("DJOBS_DB", str(tmp_path / "q.db"))`.
  Never read or write the real workspace or `~/.djobs`.
- MCP tool tests call the tool functions directly (no MCP transport) after
  `configure(db_path)`; assert on `json.loads(result)`.
- Because `_job_to_dict` is lean, assert a field is ABSENT (`"x" not in result`)
  or use `.get("x")`; do NOT assert `result["x"] is None`.
- For PATH-dependent code, fake it: `monkeypatch.setattr("shutil.which", ...)`.
- Postgres tests `importorskip("psycopg")` and skip unless `DJOBS_TEST_PG_DSN`
  is set, so the default `pytest -q` stays green without a database.
- No network, no real subprocess installs (pipx/pip) in unit tests - stub them.
- When you ADD or CHANGE behavior, ADD/UPDATE a test in `tests/unit/` (or
  `tests/integration/` for backend-contract changes). A feature without a test
  is not done. New test files follow `tests/unit/test_<area>.py`.

## Definition of done (every change)

1. All four CI gates pass locally (lint, format, mypy, pytest) + extension
   `tsc --noEmit` and `npm run compile` if `vscode-ext/` changed.
2. Tests added/updated for the change; full suite green.
3. `CHANGELOG.md` `[Unreleased]` updated, tagged `[core]` or `[ext]`.
4. If user-facing behavior or commands changed, update `README.md`.
5. Version bumped only when releasing (see below), never mid-feature unless the
   ext/py lockstep requires it.
6. Temp `_*.py` / `_*.js` verification scripts deleted; no debug leftovers.
7. Changes left UNCOMMITTED on the work machine (the human releases elsewhere).

## Release flow (performed on the release machine `C:\dev\djobs`)

The repeatable release runbook is `docs/RELEASE.md`; follow it rather than
reconstructing steps from chat history. The GitHub Release body is copied from
the matching `CHANGELOG.md` version section, not generated from commit logs.

1. Bump `src/djobs/__init__.py` `__version__`; run
   `node vscode-ext/scripts/sync-version.js` (or `npm run compile`).
2. Move `CHANGELOG.md` `[Unreleased]` to the new `[x.y.z] - <date>` and add a
   fresh empty `[Unreleased]`.
3. `python -m build` + `twine check dist/*` to confirm the sdist/wheel.
4. Commit, then `git tag vX.Y.Z` and push the branch then the tag
   (`git push origin main` then `git push origin vX.Y.Z` - not `--tags`, to
   avoid pushing stray local tags).
5. The tag triggers `.github/workflows/publish.yml` -> PyPI via trusted
   publishing. The extension `.vsix` (`npx @vscode/vsce package`) is uploaded to
   the VS Code Marketplace separately.
6. `scripts/release.ps1 -Version X.Y.Z` automates the bump+test+build steps.
7. **MCP Registry (optional, after PyPI is live).** `server.json` describes djobs
   for the official registry. Its version is synced by `sync-version.js`, and the
   PyPI README carries the `<!-- mcp-name: io.github.jhuang-tw/djobs -->` marker
   the registry checks for package ownership. Publish with the `mcp-publisher`
   CLI (`mcp-publisher login github` then `mcp-publisher publish`) once the
   matching `djobs` version is on PyPI - the registry rejects a version that is
   not yet published. The server is launched as `uvx djobs mcp`.

### Pre-upload checklist (run before PyPI / Marketplace / Pages)

Do not upload anything until every item below is explicitly checked:

1. **Version is new and synced**
   - `src/djobs/__init__.py` has the intended new version.
   - `vscode-ext/package.json` has the same version after
     `node vscode-ext/scripts/sync-version.js`.
   - `server.json` (top-level `version` and `packages[].version`) matches too -
     `sync-version.js` updates it; `test_server_json.py` asserts it.
   - `git tag --list vX.Y.Z` returns nothing for the new version.
   - `CHANGELOG.md` has `## [X.Y.Z] - YYYY-MM-DD` and a fresh empty
     `## [Unreleased]` above it.
2. **Artifacts correspond to that exact version**
   - Python: `python -m build` creates `dist/djobs-X.Y.Z*`.
   - Python: `twine check dist/*` passes.
   - Extension: `vscode-ext/djobs-X.Y.Z.vsix` exists after packaging.
3. **All gates are green**
   - Python lint, format, mypy, and full pytest all pass.
   - Extension `npx tsc -p ./ --noEmit` and `npm run compile` pass.
4. **Release-machine git is clean except intended files**
   - No `.vscode/mcp.json`, `.github/copilot-instructions.md`, local scratch
     notes, temp scripts, `node_modules`, `out`, `.vsix`, `dist`, or cache files
     accidentally staged.
   - `AGENTS.md` and `.github/skills/djobs-development/SKILL.md` are intentional
     tracked files (contributor guidance), not local scratch.
5. **Remote metadata and Pages are aligned**
   - Repository description: `Crash-proof task memory for Claude Code, Cursor,
     Cline, Copilot agents via MCP`.
   - Topics include: `claude-code`, `cursor`, `cline`, `copilot`, `mcp-server`,
     `ai-coding-agent`, `workflow-state`.
   - Homepage points to `https://jhuang-tw.github.io/djobs/`.
   - GitHub Pages is set to GitHub Actions, and `.github/workflows/pages.yml`
     is included.
6. **Push exactly one tag**
   - Use `git push origin main` and then `git push origin vX.Y.Z`.
   - Do not use `git push --tags`; it can push stray local tags.

`scripts/release.ps1` still prints `git push --tags` in its final manual steps;
treat that output as outdated and follow this checklist instead.

## Context hygiene (keep the agent fast and correct)

- Don't read large terminal temp files back into context; pipe through
  `Select-Object -Last N`.
- Prefer `grep_search` / `file_search` over reading whole files; read targeted
  ranges. Use the `Explore` subagent for broad read-only investigation.
- After every 5 tool calls, or after making 3 or more file edits in a single
  turn, summarize progress before continuing.
