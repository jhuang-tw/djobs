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


def test_sqlite_runtime_notice_is_specific_and_not_a_coding_blocker() -> None:
    cases = {
        (3, 40, 1): False,
        (3, 44, 5): False,
        (3, 44, 6): True,
        (3, 45, 1): False,
        (3, 49, 1): False,
        (3, 50, 4): False,
        (3, 50, 7): True,
        (3, 51, 0): False,
        (3, 51, 2): False,
        (3, 51, 3): True,
        (3, 52, 0): True,
    }
    for version, patched in cases.items():
        check = entrypoint._sqlite_wal_runtime_check(version)
        assert check["ok"] is patched
        assert check["level"] == ("info" if patched else "warning")
        assert check["level"] != "check"  # Doctor warnings do not fail normal coding.
        if not patched:
            assert "No runtime or journal-mode change" in check["next_step"]
        else:
            assert "other risks are not assessed" in check["detail"]


def test_sqlite_runtime_notice_reads_only_runtime_version(monkeypatch) -> None:
    import socket
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError("runtime warning must not connect to DB or network")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 50, 4))
    assert entrypoint._sqlite_wal_runtime_check()["ok"] is False
