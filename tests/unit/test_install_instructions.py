"""Unit tests for the auto-managed djobs guidance block in copilot-instructions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from djobs import cli


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _read(workdir: Path) -> str:
    return (workdir / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")


def test_creates_file_with_managed_block(workdir: Path) -> None:
    cli._write_instructions_block()
    content = _read(workdir)
    assert content.startswith(cli._DJOBS_INSTRUCTIONS_START)
    assert content.rstrip().endswith(cli._DJOBS_INSTRUCTIONS_END)
    assert "durable job queue" in content


def test_idempotent_rerun(workdir: Path) -> None:
    cli._write_instructions_block()
    first = _read(workdir)
    cli._write_instructions_block()
    second = _read(workdir)
    assert first == second
    # Exactly one managed block.
    assert second.count(cli._DJOBS_INSTRUCTIONS_START) == 1
    assert second.count(cli._DJOBS_INSTRUCTIONS_END) == 1


def test_appends_to_existing_user_content(workdir: Path) -> None:
    gh = workdir / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text(
        "# My rules\n\n- Always use tabs.\n", encoding="utf-8"
    )
    cli._write_instructions_block()
    content = _read(workdir)
    assert "# My rules" in content
    assert "- Always use tabs." in content
    assert cli._DJOBS_INSTRUCTIONS_START in content


def test_updates_block_in_place_preserving_surroundings(workdir: Path) -> None:
    gh = workdir / ".github"
    gh.mkdir()
    stale_block = (
        f"{cli._DJOBS_INSTRUCTIONS_START}\nSTALE CONTENT\n{cli._DJOBS_INSTRUCTIONS_END}\n"
    )
    (gh / "copilot-instructions.md").write_text(
        f"# Top\n\n{stale_block}\n# Bottom\n", encoding="utf-8"
    )
    cli._write_instructions_block()
    content = _read(workdir)
    assert "STALE CONTENT" not in content
    assert "# Top" in content
    assert "# Bottom" in content
    assert content.count(cli._DJOBS_INSTRUCTIONS_START) == 1


def test_no_instructions_flag_skips_write(workdir: Path) -> None:
    # Simulate `install-mcp --no-instructions` by driving the command handler.
    args = cli.argparse.Namespace(
        full_approve=False,
        print=False,
        force=True,
        output=str(workdir / ".vscode" / "mcp.json"),
        db=None,
        use_global=False,
        write_instructions=False,
    )
    cli._cmd_install_mcp(args)
    assert not (workdir / ".github").exists()
    assert os.path.exists(workdir / ".vscode" / "mcp.json")


def test_install_mcp_writes_instructions_by_default(workdir: Path) -> None:
    args = cli.argparse.Namespace(
        full_approve=False,
        print=False,
        force=True,
        output=str(workdir / ".vscode" / "mcp.json"),
        db=None,
        use_global=False,
        write_instructions=True,
    )
    cli._cmd_install_mcp(args)
    assert (workdir / ".github" / "copilot-instructions.md").exists()
    assert cli._DJOBS_INSTRUCTIONS_START in _read(workdir)
