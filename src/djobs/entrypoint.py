"""Console entry point that makes context-efficient MCP tools the default."""

from __future__ import annotations

import argparse


def _cmd_mcp_low_token(args: argparse.Namespace) -> None:
    """Run the normal CLI ``mcp`` command through the low-token server."""

    from djobs.mcp_server import configure

    if getattr(args, "db", None):
        configure(args.db)

    from djobs.low_token_mcp import main as run_mcp_server

    run_mcp_server()


def main() -> None:
    """Run the existing CLI while replacing only its MCP handler."""

    from djobs import cli

    original = getattr(cli, "_cmd_mcp")
    setattr(cli, "_cmd_mcp", _cmd_mcp_low_token)
    try:
        cli.main()
    finally:
        setattr(cli, "_cmd_mcp", original)
