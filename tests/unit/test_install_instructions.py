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


def test_block_is_opinionated_about_evidence_and_idempotency(workdir: Path) -> None:
    # The auto-loaded block must steer agents to record evidence on completion
    # and use idempotency keys, so resume verifies work instead of redoing it.
    cli._write_instructions_block()
    content = _read(workdir)
    assert "idempotency_key" in content
    assert "evidence" in content
    assert "complete_task" in content
    assert "fail_task" in content


def test_block_requires_resume_and_plan_before_long_work(workdir: Path) -> None:
    cli._write_instructions_block()
    content = _read(workdir)
    assert "Start every coding session with `resume_session`" in content
    assert "Treat natural work requests as the trigger" in content
    assert "do not wait for them to mention" in content
    assert "Plan before editing long or multi-step work" in content
    assert "call `enqueue_task` before the first edit" in content


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


# ---------------------------------------------------------------------------
# install-instructions command
# ---------------------------------------------------------------------------


def test_install_instructions_creates_copilot_file(workdir: Path) -> None:
    cli.main(["install-instructions"])
    content = _read(workdir)
    assert content.startswith(cli._DJOBS_INSTRUCTIONS_START)
    assert cli._DJOBS_INSTRUCTIONS_END in content
    # Default target must not touch .vscode/mcp.json.
    assert not (workdir / ".vscode" / "mcp.json").exists()


def test_install_instructions_appends_without_deleting_user_content(workdir: Path) -> None:
    gh = workdir / ".github"
    gh.mkdir()
    (gh / "copilot-instructions.md").write_text(
        "# House rules\n\n- Prefer f-strings.\n", encoding="utf-8"
    )
    cli.main(["install-instructions"])
    content = _read(workdir)
    assert "# House rules" in content
    assert "- Prefer f-strings." in content
    assert content.count(cli._DJOBS_INSTRUCTIONS_START) == 1


def test_install_instructions_replaces_block_idempotently(workdir: Path) -> None:
    cli.main(["install-instructions"])
    first = _read(workdir)
    cli.main(["install-instructions"])
    second = _read(workdir)
    assert first == second
    assert second.count(cli._DJOBS_INSTRUCTIONS_START) == 1
    assert second.count(cli._DJOBS_INSTRUCTIONS_END) == 1


def test_install_instructions_target_agent_md(workdir: Path) -> None:
    cli.main(["install-instructions", "--target", "agent-md"])
    agent = workdir / ".agent.md"
    assert agent.exists()
    assert cli._DJOBS_INSTRUCTIONS_START in agent.read_text(encoding="utf-8")
    # Copilot file must NOT be created for the agent-md target.
    assert not (workdir / ".github" / "copilot-instructions.md").exists()


def test_install_instructions_target_all(workdir: Path) -> None:
    cli.main(["install-instructions", "--target", "all"])
    copilot = workdir / ".github" / "copilot-instructions.md"
    agent = workdir / ".agent.md"
    assert copilot.exists()
    assert agent.exists()
    assert cli._DJOBS_INSTRUCTIONS_START in copilot.read_text(encoding="utf-8")
    assert cli._DJOBS_INSTRUCTIONS_START in agent.read_text(encoding="utf-8")


def test_install_instructions_print_writes_stdout_no_files(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["install-instructions", "--print"])
    out = capsys.readouterr().out
    assert cli._DJOBS_INSTRUCTIONS_START in out
    assert cli._DJOBS_INSTRUCTIONS_END in out
    assert "durable job queue" in out
    # --print must not create any instruction files.
    assert not (workdir / ".github").exists()
    assert not (workdir / ".agent.md").exists()


# ---------------------------------------------------------------------------
# init command (one-command onboarding)
# ---------------------------------------------------------------------------


def test_init_creates_mcp_json(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJOBS_DB", str(workdir / "q.db"))
    cli.main(["init"])
    assert (workdir / ".vscode" / "mcp.json").exists()


def test_init_creates_default_instruction_file(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DJOBS_DB", str(workdir / "q.db"))
    cli.main(["init"])
    copilot = workdir / ".github" / "copilot-instructions.md"
    assert copilot.exists()
    assert cli._DJOBS_INSTRUCTIONS_START in copilot.read_text(encoding="utf-8")


def test_init_prints_next_steps(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DJOBS_DB", str(workdir / "q.db"))
    cli.main(["init"])
    out = capsys.readouterr().out
    assert "djobs is initialized." in out
    assert "resume_session" in out


def test_init_instructions_target_all_writes_both(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DJOBS_DB", str(workdir / "q.db"))
    cli.main(["init", "--instructions-target", "all"])
    assert (workdir / ".github" / "copilot-instructions.md").exists()
    assert (workdir / ".agent.md").exists()


def test_init_default_mcp_json_has_no_global_env(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.setenv("DJOBS_DB", str(workdir / "q.db"))
    cli.main(["init"])
    data = json.loads((workdir / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    server = data["servers"]["djobs"]
    # Default init must not wire a DJOBS_DB env (that's opt-in via --db/--global).
    assert "env" not in server
