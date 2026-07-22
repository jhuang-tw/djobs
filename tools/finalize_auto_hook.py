"""One-shot finalizer for automatic hooks and gain analytics.

This file is executed by the temporary PR job and deleted in the resulting
product commit.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from textwrap import dedent


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected exactly one match for {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def make_migration_indentation_aware() -> None:
    path = Path("tools/apply_auto_hook_compat.py")
    text = path.read_text(encoding="utf-8")
    old = dedent(
        '''
        def replace_once(path: str, old: str, new: str) -> None:
            target = Path(path)
            text = target.read_text(encoding="utf-8")
            count = text.count(old)
            if count != 1:
                raise RuntimeError(f"{path}: expected one match, found {count}: {old[:80]!r}")
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
        '''
    )
    new = dedent(
        '''
        def replace_once(path: str, old: str, new: str) -> None:
            target = Path(path)
            text = target.read_text(encoding="utf-8")
            count = text.count(old)
            if count == 1:
                target.write_text(text.replace(old, new, 1), encoding="utf-8")
                return
            if count > 1:
                raise RuntimeError(
                    f"{path}: expected one match, found {count}: {old[:80]!r}"
                )

            source_lines = text.splitlines(keepends=True)
            old_lines = old.strip("\\n").splitlines()
            normalized_old = [line.lstrip() for line in old_lines]
            matches: list[tuple[int, int, str]] = []
            for start in range(len(source_lines) - len(old_lines) + 1):
                segment = source_lines[start : start + len(old_lines)]
                normalized_segment = [line.rstrip("\\r\\n").lstrip() for line in segment]
                if normalized_segment != normalized_old:
                    continue
                first = next((line for line in segment if line.strip()), "")
                indent = first[: len(first) - len(first.lstrip())]
                matches.append((start, start + len(old_lines), indent))
            if len(matches) != 1:
                raise RuntimeError(
                    f"{path}: expected one indentation-insensitive match, "
                    f"found {len(matches)}: {old[:80]!r}"
                )

            start, end, indent = matches[0]
            replacement_lines = dedent(new).strip("\\n").splitlines()
            replacement = "\\n".join(
                f"{indent}{line}" if line else "" for line in replacement_lines
            ) + "\\n"
            source_lines[start:end] = [replacement]
            target.write_text("".join(source_lines), encoding="utf-8")
        '''
    )
    if text.count(old) != 1:
        raise RuntimeError("migration replace_once implementation changed unexpectedly")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def fix_generated_indentation() -> None:
    replace_once(
        "src/djobs/auto_hook.py",
        '\n# Copilot CLI/cloud consumes the top-level fields. VS Code consumes\n'
        '# hookSpecificOutput.updatedInput. Returning both makes the same hook\n'
        '# file deterministic across hosts; unknown fields are ignored.\n'
        'return {\n'
        '    "permissionDecision": "allow",\n'
        '    "modifiedArgs": args,\n'
        '    "hookSpecificOutput": {\n'
        '        "hookEventName": "PreToolUse",\n'
        '        "permissionDecision": "allow",\n'
        '        "updatedInput": args,\n'
        '    },\n'
        '}\n',
        '\n    # Copilot CLI/cloud consumes the top-level fields. VS Code consumes\n'
        '    # hookSpecificOutput.updatedInput. Returning both makes the same hook\n'
        '    # file deterministic across hosts; unknown fields are ignored.\n'
        '    return {\n'
        '        "permissionDecision": "allow",\n'
        '        "modifiedArgs": args,\n'
        '        "hookSpecificOutput": {\n'
        '            "hookEventName": "PreToolUse",\n'
        '            "permissionDecision": "allow",\n'
        '            "updatedInput": args,\n'
        '        },\n'
        '    }\n',
    )
    replace_once(
        "src/djobs/auto_hook.py",
        '\ncontext = "\\n".join(lines)\n'
        'return {\n'
        '    "additionalContext": context,\n'
        '    "hookSpecificOutput": {\n'
        '        "hookEventName": "SessionStart",\n'
        '        "additionalContext": context,\n'
        '    },\n'
        '}\n',
        '\n    context = "\\n".join(lines)\n'
        '    return {\n'
        '        "additionalContext": context,\n'
        '        "hookSpecificOutput": {\n'
        '            "hookEventName": "SessionStart",\n'
        '            "additionalContext": context,\n'
        '        },\n'
        '    }\n',
    )
    replace_once(
        "src/djobs/auto_hook.py",
        '\ndb_group = install_parser.add_mutually_exclusive_group()\n'
        'db_group.add_argument(\n'
        '    "--db",\n'
        '    default=None,\n'
        '    help="Use this SQLite database for both automatic hooks and MCP.",\n'
        ')\n'
        'db_group.add_argument(\n'
        '    "--global",\n'
        '    dest="use_global",\n'
        '    action="store_true",\n'
        '    help="Use the shared queue at ~/.djobs/global.db.",\n'
        ')\n'
        'install_parser.add_argument("--force", action="store_true")\n',
        '\n    db_group = install_parser.add_mutually_exclusive_group()\n'
        '    db_group.add_argument(\n'
        '        "--db",\n'
        '        default=None,\n'
        '        help="Use this SQLite database for both automatic hooks and MCP.",\n'
        '    )\n'
        '    db_group.add_argument(\n'
        '        "--global",\n'
        '        dest="use_global",\n'
        '        action="store_true",\n'
        '        help="Use the shared queue at ~/.djobs/global.db.",\n'
        '    )\n'
        '    install_parser.add_argument("--force", action="store_true")\n',
    )
    replace_once(
        "src/djobs/auto_hook.py",
        '    if args.hook_command == "install":\n\n'
        'hook_db = Path.home() / ".djobs" / "global.db" if args.use_global else args.db\n'
        'install_hooks(\n'
        '    Path(args.root),\n'
        '    mode=args.mode,\n'
        '    force=args.force,\n'
        '    db_path=hook_db,\n'
        ')\n'
        '        return 0\n',
        '    if args.hook_command == "install":\n'
        '        hook_db = (\n'
        '            Path.home() / ".djobs" / "global.db"\n'
        '            if args.use_global\n'
        '            else args.db\n'
        '        )\n'
        '        install_hooks(\n'
        '            Path(args.root),\n'
        '            mode=args.mode,\n'
        '            force=args.force,\n'
        '            db_path=hook_db,\n'
        '        )\n'
        '        return 0\n',
    )
    replace_once(
        "src/djobs/entrypoint.py",
        '\n\nhook_db = cli._global_db() if args.use_global else getattr(args, "db", None)\n'
        'install_hooks(\n'
        '    Path.cwd(),\n'
        '    mode="smart",\n'
        '    force=args.force,\n'
        '    db_path=hook_db,\n'
        ')\n',
        '\n\n    hook_db = cli._global_db() if args.use_global else getattr(args, "db", None)\n'
        '    install_hooks(\n'
        '        Path.cwd(),\n'
        '        mode="smart",\n'
        '        force=args.force,\n'
        '        db_path=hook_db,\n'
        '    )\n',
    )


def add_gain_entrypoint() -> None:
    replace_once(
        "src/djobs/entrypoint.py",
        '    if len(sys.argv) > 1 and sys.argv[1] == "hook":\n'
        '        from djobs.auto_hook import main as run_hook_cli\n\n'
        '        raise SystemExit(run_hook_cli(sys.argv[2:]))\n',
        '    if len(sys.argv) > 1 and sys.argv[1] == "hook":\n'
        '        from djobs.auto_hook import main as run_hook_cli\n\n'
        '        raise SystemExit(run_hook_cli(sys.argv[2:]))\n\n'
        '    if len(sys.argv) > 1 and sys.argv[1] in {"gain", "stats", "state"}:\n'
        '        from djobs.gain import main as run_gain_cli\n\n'
        '        raise SystemExit(run_gain_cli(sys.argv[2:]))\n',
    )


def add_gain_docs() -> None:
    path = Path("README.md")
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "**Measured on this repo:** `djobs token-savings` estimated 12,136 replay/re-plan tokens avoided across 20 completed workflow tasks (82.6% less context to replay). The model is explicit and reproducible: `djobs token-savings --correlation-id <workspace> --format json`.",
        "See the value directly with `djobs gain`. It reports estimated tokens saved over the last 24 hours, 30 days, and all time, split between automatic command checkpoints and structured workflows. The estimate is explicit and exportable rather than presented as provider billing data.",
    )
    text = text.replace(
        "djobs hook install          # write only .github/hooks/djobs.json\n"
        "djobs doctor                # diagnose an existing setup",
        "djobs hook install          # write only .github/hooks/djobs.json\n"
        "djobs gain                  # show 24h / 30d / all-time token savings\n"
        "djobs doctor                # diagnose an existing setup",
    )
    marker = (
        "Successful automatic command checkpoints are archived after their audit evidence\n"
        "is recorded, keeping the active sidebar clean. Failed or interrupted checkpoints\n"
        "remain visible and are injected into the next session automatically.\n"
    )
    gain_docs = marker + """

### Token Savings Analytics

Like RTK's `gain` view, djobs makes its value visible instead of asking users to
trust a marketing percentage:

```bash
djobs gain                         # current workspace: 24h / 30d / all time
djobs gain --graph                 # 30-day ASCII graph
djobs gain --history               # recent records and their estimated savings
djobs gain --daily                 # non-empty day-by-day totals
djobs gain --all --format json     # every workspace, machine-readable export
```

`djobs stats` and `djobs state` are aliases for the same report.

The report separates **automatic hook savings** from **durable workflow savings**
and also shows unfinished or failed checkpoints whose compact context is protected
for recovery. Numbers estimate avoided replay, re-reading, and re-planning using a
published formula (`4` characters per token and `600` re-plan tokens per completed
record by default). They are intentionally labeled estimates, not API billing data.
"""
    if text.count(marker) != 1:
        raise RuntimeError("README automatic hook marker changed unexpectedly")
    text = text.replace(marker, gain_docs, 1)
    text = text.replace(
        "- **`djobs token-savings`** — Estimate how many replay/re-plan tokens a workflow\n"
        "  avoids because completed task state and evidence are durable. Example:\n"
        "  `djobs token-savings --correlation-id C:\\my\\repo --format json`.",
        "- **`djobs gain`** — RTK-style 24h / 30d / all-time savings analytics with source\n"
        "  breakdowns, daily history, an ASCII graph, and JSON export. The older\n"
        "  `djobs token-savings` command remains available for one-workflow estimates.",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    make_migration_indentation_aware()
    runpy.run_path("tools/apply_auto_hook_compat.py", run_name="__main__")
    fix_generated_indentation()
    add_gain_entrypoint()
    add_gain_docs()
    print("Finalized automatic hooks and gain analytics.")


if __name__ == "__main__":
    main()
