"""Release, Pages, Marketplace, and documentation guardrails.

These tests catch drift that is easy to miss locally but painful after a release:
missing public assets, manifest mismatches, generated release notes replacing the
curated changelog, and stale duplicate documentation returning to the repository.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import djobs

_REPO = Path(__file__).resolve().parents[2]
_DOCS = _REPO / "docs"
_CHANGELOG = _REPO / "CHANGELOG.md"
_EXT_PACKAGE = _REPO / "vscode-ext" / "package.json"

_CANONICAL_DOCS = (
    "README.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "docs/RELEASE.md",
    "docs/index.html",
)

_REMOVED_SURFACES = (
    "CLAUDE.md",
    ".agent.md",
    ".github/agents/durable-coder.agent.md",
    ".github/skills/djobs-development/SKILL.md",
    "docs/ARCHITECTURE.md",
    "docs/CONTEXT_EFFICIENCY.md",
    "docs/HANDOFF.md",
    "docs/IMPLEMENTATION_NOTES.md",
    "docs/INTERNALS.md",
    "docs/ROADMAP.md",
    "CHANGES.diff",
    "_pack_080.ps1",
    "scripts/backfill_dev_tracking.py",
    "scripts/release.ps1",
)


def test_pages_seo_files_exist() -> None:
    assert (_DOCS / "index.html").is_file()
    assert (_DOCS / "robots.txt").is_file()
    assert (_DOCS / "sitemap.xml").is_file()


def test_canonical_documentation_is_small_and_nonduplicated() -> None:
    for relative in _CANONICAL_DOCS:
        assert (_REPO / relative).is_file(), f"missing canonical surface: {relative}"

    for relative in _REMOVED_SURFACES:
        assert not (_REPO / relative).exists(), f"stale duplicate surface returned: {relative}"

    current_text = "\n".join(
        (_REPO / relative).read_text(encoding="utf-8") for relative in _CANONICAL_DOCS
    )
    for relative in _REMOVED_SURFACES:
        assert relative not in current_text, f"canonical docs link to removed surface: {relative}"


def test_changelog_has_current_version_section() -> None:
    text = _CHANGELOG.read_text(encoding="utf-8")
    heading = rf"^## \[{re.escape(djobs.__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$"
    assert re.search(heading, text, re.MULTILINE), (
        f"CHANGELOG.md must contain a dated section for {djobs.__version__}; "
        "GitHub Release notes are copied from it."
    )


def test_changelog_current_section_has_release_notes() -> None:
    text = _CHANGELOG.read_text(encoding="utf-8")
    match = re.search(
        rf"^## \[{re.escape(djobs.__version__)}\] - .*?\n(?P<body>.*?)(?=^## \[|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    body = match.group("body").strip()
    assert len(body) > 120
    assert re.search(r"^### ", body, re.MULTILINE)


def test_marketplace_metadata_matches_product_positioning() -> None:
    package = json.loads(_EXT_PACKAGE.read_text(encoding="utf-8"))
    assert package["version"] == djobs.__version__
    assert package["displayName"] == "djobs — Agent Checkpoints"
    assert "Crash-proof checkpoints" in package["description"]
    assert "Machine Learning" in package["categories"]
    assert 1 <= len(package["keywords"]) <= 30
    assert package["homepage"] == "https://jhuang-tw.github.io/djobs/"
    assert package["bugs"]["url"] == "https://github.com/jhuang-tw/djobs/issues"
    assert package["pricing"] == "Free"


def test_extension_prompt_actions_are_opt_in() -> None:
    # Locked product rule: prompt actions are off by default and the old
    # auto-takeover / auto-prompt commands must never come back. Behavioural
    # essentials only — not every internal symbol or menu-when string.
    package = json.loads(_EXT_PACKAGE.read_text(encoding="utf-8"))
    manifest_text = json.dumps(package, sort_keys=True)
    assert "autoTakeover" not in manifest_text
    assert "djobs.startWorkflow" not in manifest_text

    prompt_setting = package["contributes"]["configuration"]["properties"][
        "djobs.promptActions.enabled"
    ]
    assert prompt_setting["default"] is False

    commands = {item["command"] for item in package["contributes"]["commands"]}
    assert "djobs.enablePromptActions" in commands
    assert "djobs.disablePromptActions" in commands
