"""Console entry point with context-efficient MCP and automatic agent hooks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def _cmd_mcp_context_efficient(args: argparse.Namespace) -> None:
    """Run the normal CLI ``mcp`` command through the delta-context server."""

    from djobs.mcp_server import configure

    if getattr(args, "db", None):
        configure(args.db)

    from djobs.delta_mcp import main as run_mcp_server

    run_mcp_server()


def _cmd_init_with_hooks(
    args: argparse.Namespace,
    cli: Any,
    original_doctor: Any,
) -> None:
    """Run normal onboarding plus deterministic lifecycle hooks."""

    from djobs.auto_hook import install_hooks, print_hook_doctor

    mcp_target = Path(args.output)
    if mcp_target.exists() and not args.force:
        print(f"MCP wiring already present at {mcp_target} (use --force to rewrite).")
    else:
        mcp_args = argparse.Namespace(
            full_approve=args.full_approve,
            print=False,
            force=args.force,
            output=args.output,
            db=getattr(args, "db", None),
            use_global=args.use_global,
            python=args.python,
            command=args.command,
            portable=args.portable,
            write_instructions=False,
        )
        cli._cmd_install_mcp(mcp_args)

    for target in cli._resolve_instruction_targets(args.instructions_target):
        cli._write_instructions_to(target)

    hook_db = cli._global_db() if args.use_global else getattr(args, "db", None)
    install_hooks(
        Path.cwd(),
        mode="smart",
        force=args.force,
        db_path=hook_db,
    )

    print()
    original_doctor(argparse.Namespace(as_json=False))
    print_hook_doctor(Path.cwd())

    print(
        "\ndjobs is initialized with automatic hooks.\n\n"
        "Next steps:\n"
        "1. Restart VS Code / your agent host so it reloads MCP and hook configuration.\n"
        "2. Start a new agent session; unfinished checkpoints are injected automatically.\n"
        "3. Meaningful Bash/PowerShell commands are rewritten before execution and "
        "checkpointed automatically."
    )


def main() -> None:
    """Run the CLI, routing hook events before normal argparse handling."""

    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        from djobs.auto_hook import main as run_hook_cli

        raise SystemExit(run_hook_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] in {"gain", "stats", "state"}:
        from djobs.gain import main as run_gain_cli

        raise SystemExit(run_gain_cli(sys.argv[2:]))

    from djobs import cli
    from djobs.auto_hook import print_hook_doctor

    original_mcp = cli._cmd_mcp
    original_init = cli._cmd_init
    original_doctor = cli._cmd_doctor

    def doctor_with_hooks(args: argparse.Namespace) -> None:
        original_doctor(args)
        if not getattr(args, "as_json", False):
            print_hook_doctor(Path.cwd())

    def init_with_hooks(args: argparse.Namespace) -> None:
        _cmd_init_with_hooks(args, cli, original_doctor)

    cli._cmd_mcp = _cmd_mcp_context_efficient
    cli._cmd_init = init_with_hooks
    cli._cmd_doctor = doctor_with_hooks
    try:
        cli.main()
    finally:
        cli._cmd_mcp = original_mcp
        cli._cmd_init = original_init
        cli._cmd_doctor = original_doctor
