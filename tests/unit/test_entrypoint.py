"""Tests for the default context-efficient console entry point."""

from __future__ import annotations

import argparse

from djobs import entrypoint


def test_main_temporarily_replaces_only_the_mcp_handler(monkeypatch):
    from djobs import cli

    original = cli._cmd_mcp
    observed = []

    def fake_cli_main() -> None:
        observed.append(cli._cmd_mcp)

    monkeypatch.setattr(cli, "main", fake_cli_main)
    entrypoint.main()

    assert observed == [entrypoint._cmd_mcp_context_efficient]
    assert cli._cmd_mcp is original


def test_context_efficient_mcp_handler_honors_db_override(monkeypatch):
    from djobs import delta_mcp, mcp_server

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(mcp_server, "configure", lambda db: calls.append(("configure", db)))
    monkeypatch.setattr(delta_mcp, "main", lambda: calls.append(("run", None)))

    entrypoint._cmd_mcp_context_efficient(argparse.Namespace(db="custom.db"))

    assert calls == [("configure", "custom.db"), ("run", None)]
