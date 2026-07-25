"""Tests for the default context-efficient console entry point."""

from __future__ import annotations

import argparse

from djobs import entrypoint


def test_legacy_main_temporarily_replaces_only_the_mcp_handler(monkeypatch):
    from djobs import cli

    original = cli._cmd_mcp
    observed = []

    def fake_cli_main(argv=None) -> None:
        observed.append((cli._cmd_mcp, list(argv or [])))

    monkeypatch.setattr(cli, "main", fake_cli_main)
    monkeypatch.setattr(entrypoint.sys, "argv", ["djobs", "legacy"])
    entrypoint.main()

    assert observed == [(entrypoint._cmd_mcp_context_efficient, ["--help"])]
    assert cli._cmd_mcp is original


def test_context_efficient_mcp_handler_honors_db_override(monkeypatch):
    from djobs import coding_mcp, mcp_server

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(mcp_server, "configure", lambda db: calls.append(("configure", db)))
    monkeypatch.setattr(coding_mcp, "main", lambda: calls.append(("run", None)))

    entrypoint._cmd_mcp_context_efficient(argparse.Namespace(db="custom.db"))

    assert calls == [("configure", "custom.db"), ("run", None)]


def test_version_flag_prints_package_version(monkeypatch, capsys):
    import djobs

    monkeypatch.setattr(entrypoint.sys, "argv", ["djobs", "--version"])
    entrypoint.main()

    assert capsys.readouterr().out.strip() == f"djobs {djobs.__version__}"


def test_setup_action_is_routed_without_duplicate_action(monkeypatch):
    import djobs.setup_cli as setup_cli

    observed: list[tuple[list[str], str | None]] = []

    def fake_setup(argv, *, action=None):
        observed.append((list(argv), action))
        return 0

    monkeypatch.setattr(setup_cli, "main", fake_setup)
    monkeypatch.setattr(entrypoint.sys, "argv", ["djobs", "setup", "copilot"])

    try:
        entrypoint.main()
    except SystemExit as exc:
        assert exc.code == 0

    assert observed == [(["copilot"], "setup")]
