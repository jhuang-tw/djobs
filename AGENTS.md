# AGENTS.md - working on the djobs codebase

Guidance for any AI agent editing **this repository** (the djobs Python package
and VS Code extension). For *using* djobs at runtime, see the README and the
Durable Coder agent (`.github/agents/durable-coder.agent.md`) instead.

> Full detail, with the reasoning behind each item, lives in the
> **`djobs-development`** skill: [.github/skills/djobs-development/SKILL.md](.github/skills/djobs-development/SKILL.md).
> Read it before a non-trivial change.

## Commonly-hit gotchas (the short list)

1. **Don't commit here.** `c:\src\my\djobs` is the work machine (edit only); the
   human commits/pushes from the release machine `C:\dev\djobs`. Leave changes
   uncommitted unless told otherwise.
2. **Version = one place.** Bump only `src/djobs/__init__.py` `__version__`, then
   run `node vscode-ext/scripts/sync-version.js` so the extension matches.
   `pyproject.toml` is dynamic - never hardcode a version. Never reuse an
   already-tagged version (v0.7.0-v0.7.3 exist).
3. **Two ruff gates.** CI runs `ruff check` **and** `ruff format --check`.
   Passing one is not enough - run both (apply with `ruff format src/ tests/`).
4. **PowerShell breaks `python -c "..."`** with escaped quotes (and heredocs leave
   a stuck `>>>` REPL). Write a temp `.py` file, run it, delete it.
5. **CJK/emoji edits corrupt.** Never use `multi_replace_string_in_file` on text
   containing CJK or 4-byte emoji; use a single `replace_string_in_file` and
   grep for mojibake. Keep new repo files English (the repo is English-only).
6. **argparse dest collisions.** A flag `dest` must not shadow
   `add_subparsers(dest=...)` (this caused `install-mcp`/`init` to mis-set
   `DJOBS_DB`; the subparser dest is now `subcommand`).
7. **MCP tool output is agent tokens.** Return compact JSON (`_dumps`), keep
   `_job_to_dict` lean (omits empty fields) - so don't assert a field `== None`
   in tests; assert absence or use `.get()`.
8. **`submit()` auto-generates a UUID `correlation_id`** when given `None`.
9. **doctor uses ASCII marks** `[OK  ]/[INFO]/[FAIL]` (no unicode check mark); advisory
   checks are `[INFO]`, never `[FAIL]`. A clean `djobs init` shows zero `[FAIL]`.
10. **Use `rowid`, not `created_at`,** for "earlier tasks" ordering (Windows
    clock-tick collisions miss same-tick rows).
11. **Before upload/release, check version + artifacts.** New version must be
   unique (`git tag --list vX.Y.Z` empty), `src/djobs/__init__.py` and
   `vscode-ext/package.json` must match, `CHANGELOG.md` must have a dated
   release section, `twine check dist/*` must pass, and the `.vsix` filename
   must match the same version. Push exactly one tag (`git push origin vX.Y.Z`),
   not `git push --tags`.

## Verify before done

```powershell
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe -m ruff format --check src/ tests/
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
Push-Location vscode-ext; npx tsc -p ./ --noEmit; npm run compile; Pop-Location
```
