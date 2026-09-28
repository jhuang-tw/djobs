from __future__ import annotations

from djobs.mcp_launch import resolve_compact_mcp_launch


def test_default_prefers_the_public_djobs_mcp_subcommand() -> None:
    launch = resolve_compact_mcp_launch(
        which=lambda name: "/tools/djobs" if name == "djobs" else None,
        scripts_directory="/tools",
    )

    assert launch.command == "/tools/djobs"
    assert launch.args == ("mcp",)
    assert launch.source == "public-command"


def test_default_falls_back_to_the_same_public_module_dispatcher() -> None:
    launch = resolve_compact_mcp_launch(
        which=lambda _name: None,
        executable="/python",
        scripts_directory="/missing-scripts",
    )

    assert launch.argv() == ["/python", "-m", "djobs.public_cli", "mcp"]
    assert launch.source == "current-python"


def test_explicit_and_portable_launches_preserve_precedence() -> None:
    explicit = resolve_compact_mcp_launch(command="djobs-mcp", python="ignored")
    assert explicit.argv() == ["djobs-mcp"]

    python = resolve_compact_mcp_launch(python="/custom/python")
    assert python.argv() == ["/custom/python", "-m", "djobs.public_cli", "mcp"]

    windows = resolve_compact_mcp_launch(portable=True, platform_name="nt")
    assert windows.argv() == [
        "${workspaceFolder}/.venv/Scripts/python",
        "-m",
        "djobs.public_cli",
        "mcp",
    ]

    posix = resolve_compact_mcp_launch(portable=True, platform_name="posix")
    assert posix.command == "${workspaceFolder}/.venv/bin/python"


def test_unrelated_path_installation_is_not_used() -> None:
    launch = resolve_compact_mcp_launch(
        which=lambda _name: "/global/bin/djobs",
        executable="/project/venv/bin/python",
        scripts_directory="/project/venv/bin",
    )

    assert launch.argv() == [
        "/project/venv/bin/python",
        "-m",
        "djobs.public_cli",
        "mcp",
    ]


def test_existing_sibling_command_wins_without_path_lookup(tmp_path) -> None:
    command = tmp_path / "djobs"
    command.write_text("fixture", encoding="utf-8")

    launch = resolve_compact_mcp_launch(
        which=lambda _name: (_ for _ in ()).throw(AssertionError("PATH lookup was unnecessary")),
        scripts_directory=tmp_path,
        platform_name="posix",
    )

    assert launch.argv() == [str(command), "mcp"]
