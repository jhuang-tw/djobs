"""Console entry point that makes context-efficient MCP tools the default."""

from __future__ import annotations

import argparse


def _cmd_mcp_context_efficient(args: argparse.Namespace) -> None:
    """Run the normal CLI ``mcp`` command through the delta-context server."""

    from djobs.mcp_server import configure

    if getattr(args, "db", None):
        configure(args.db)

    from djobs.delta_mcp import main as run_mcp_server

    run_mcp_server()


def main() -> None:
    """Run the existing CLI while replacing only its MCP handler."""

    from djobs import cli

    original = cli._cmd_mcp
    cli._cmd_mcp = _cmd_mcp_context_efficient
    try:
        cli.main()
    finally:
        cli._cmd_mcp = original
