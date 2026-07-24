from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
PLANNER = runpy.run_path(str(ROOT / "scripts" / "prepare_auto_release.py"))

Commit = PLANNER["Commit"]
bump_version = cast(Any, PLANNER["bump_version"])
classify_bump = cast(Any, PLANNER["classify_bump"])
parse_version = cast(Any, PLANNER["parse_version"])
render_changelog_section = cast(Any, PLANNER["render_changelog_section"])


def test_semantic_version_bumps() -> None:
    assert parse_version("0.16.0") == (0, 16, 0)
    assert bump_version("0.16.0", "patch") == "0.16.1"
    assert bump_version("0.16.0", "minor") == "0.17.0"
    assert bump_version("0.16.0", "major") == "1.0.0"


def test_commit_types_select_highest_required_bump() -> None:
    fixes = (Commit("a" * 40, "fix: avoid duplicate replay", ""),)
    features = (*fixes, Commit("b" * 40, "feat(memory): share worktree context", ""))
    breaking = (
        *features,
        Commit("c" * 40, "refactor!: replace the public queue contract", ""),
    )

    assert classify_bump(fixes) == "patch"
    assert classify_bump(features) == "minor"
    assert classify_bump(breaking) == "major"


def test_breaking_change_footer_selects_major() -> None:
    commits = (
        Commit(
            "d" * 40,
            "refactor: simplify storage",
            "BREAKING CHANGE: old database adapters must migrate",
        ),
    )
    assert classify_bump(commits) == "major"


def test_generated_changelog_is_grouped_and_dated() -> None:
    commits = (
        Commit("a" * 40, "feat: compile minimal context", ""),
        Commit("b" * 40, "fix(mcp): suppress unchanged replay", ""),
        Commit("c" * 40, "docs: explain automatic releases", ""),
    )

    section = render_changelog_section("0.17.0", "2026-07-24", commits)

    assert section.startswith("## [0.17.0] - 2026-07-24")
    assert "### Added\n- `[release]` Compile minimal context" in section
    assert "### Changed\n- `[release]` Explain automatic releases" in section
    assert "### Fixed\n- `[release]` Suppress unchanged replay" in section
