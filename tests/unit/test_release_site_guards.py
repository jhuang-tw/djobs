"""Release, Pages, and SEO guardrails.

These tests catch mistakes that are easy to miss locally but painful after a
release: missing sitemap/robots deployment, generated GitHub Release notes
replacing the curated changelog, and version sections missing from the
changelog. They run in the normal pytest job, so publishing invariants fail in CI
before a tag is pushed.
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


def test_pages_seo_files_exist() -> None:
    assert (_DOCS / "index.html").is_file()
    assert (_DOCS / "robots.txt").is_file()
    assert (_DOCS / "sitemap.xml").is_file()


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
