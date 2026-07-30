"""Tests for the default context-efficient console entry point."""

from __future__ import annotations

import argparse

from djobs import entrypoint


def test_legacy_main_temporarily_replaces_only_the_mcp_handler(monkeypatch):
    from djobs import cli

    original = cli._cmd_mcp
    observed = []

    def fake_cli_main(argv=None, *, prog="djobs") -> None:
        observed.append((cli._cmd_mcp, list(argv or []), prog))

    monkeypatch.setattr(cli, "main", fake_cli_main)
    monkeypatch.setattr(entrypoint.sys, "argv", ["djobs", "legacy"])
    entrypoint.main()

    assert observed == [(entrypoint._cmd_mcp_context_efficient, ["--help"], "djobs legacy")]
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


def test_storage_check_and_backup_commands_use_shared_database(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "shared.db"
    backup = tmp_path / "shared.backup.db"
    monkeypatch.setenv("DJOBS_DB", str(database))

    assert entrypoint._run_storage(["check", "--json"]) == 0
    check = __import__("json").loads(capsys.readouterr().out)
    assert check["ok"] is True
    assert check["database_path"] == str(database.resolve())

    assert entrypoint._run_storage(["backup", str(backup), "--json"]) == 0
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["created"] is True
    assert backup.exists()


def test_storage_command_routes_from_public_entrypoint(monkeypatch) -> None:
    observed: list[list[str]] = []
    monkeypatch.setattr(entrypoint, "_run_storage", lambda argv: observed.append(list(argv)) or 0)
    monkeypatch.setattr(entrypoint.sys, "argv", ["djobs", "storage", "check"])

    try:
        entrypoint.main()
    except SystemExit as exc:
        assert exc.code == 0

    assert observed == [["check"]]
