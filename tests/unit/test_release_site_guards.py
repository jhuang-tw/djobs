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

import yaml

import djobs

_REPO = Path(__file__).resolve().parents[2]
_DOCS = _REPO / "docs"
_PAGES_WORKFLOW = _REPO / ".github" / "workflows" / "pages.yml"
_PUBLISH_WORKFLOW = _REPO / ".github" / "workflows" / "publish.yml"
_CHANGELOG = _REPO / "CHANGELOG.md"
_EXT_PACKAGE = _REPO / "vscode-ext" / "package.json"
_EXT_SOURCE_FILES = [
    _REPO / "vscode-ext" / "src" / "extension.ts",
    _REPO / "vscode-ext" / "src" / "djobsClient.ts",
    _REPO / "vscode-ext" / "src" / "tasksProvider.ts",
]


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _workflow_on(workflow: dict) -> dict:
    """Return a workflow's ``on`` block across YAML 1.1/1.2 parsers.

    PyYAML still treats the key ``on`` as boolean ``True`` by default. GitHub
    Actions treats it as the literal string. Accept both so the guard tests are
    checking our workflow, not a parser quirk.
    """
    return workflow.get("on") or workflow[True]


def test_pages_seo_files_exist() -> None:
    assert (_DOCS / "index.html").is_file()
    assert (_DOCS / "robots.txt").is_file()
    assert (_DOCS / "sitemap.xml").is_file()


def test_robots_points_to_pages_sitemap() -> None:
    robots = (_DOCS / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert "Sitemap: https://jhuang-tw.github.io/djobs/sitemap.xml" in robots


def test_sitemap_points_to_canonical_pages_url() -> None:
    sitemap = (_DOCS / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://jhuang-tw.github.io/djobs/</loc>" in sitemap
    assert "<lastmod>" in sitemap


def test_pages_workflow_deploys_seo_files() -> None:
    workflow = _load_yaml(_PAGES_WORKFLOW)
    paths = _workflow_on(workflow)["push"]["paths"]
    assert "docs/robots.txt" in paths
    assert "docs/sitemap.xml" in paths

    prepare = workflow["jobs"]["deploy"]["steps"][1]
    script = prepare["run"]
    assert "cp docs/robots.txt _site/robots.txt" in script
    assert "cp docs/sitemap.xml _site/sitemap.xml" in script


def test_publish_workflow_uses_changelog_not_generated_notes() -> None:
    text = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "actions/ai-inference" not in text
    assert "generate_release_notes: true" not in text
    assert "Prepare release body from CHANGELOG.md" in text
    assert "generate_release_notes: false" in text
    assert "body_path: release_body.md" in text


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
    package = json.loads(_EXT_PACKAGE.read_text(encoding="utf-8"))
    manifest_text = json.dumps(package, sort_keys=True)
    forbidden_manifest_strings = [
        "djobs.startWorkflow",
        "djobs.resumeAll",
        "djobs.resumeWorkflow",
        "djobs.resumeFromHere",
        "djobs.autoTakeoverPrompt",
        "djobs.autoTakeoverMode",
    ]
    for forbidden in forbidden_manifest_strings:
        assert forbidden not in manifest_text

    prompt_setting = package["contributes"]["configuration"]["properties"][
        "djobs.promptActions.enabled"
    ]
    assert prompt_setting["default"] is False

    commands = {item["command"] for item in package["contributes"]["commands"]}
    assert "djobs.enablePromptActions" in commands
    assert "djobs.disablePromptActions" in commands

    title_menus = package["contributes"]["menus"]["view/title"]
    enable_menu = next(
        item for item in title_menus if item["command"] == "djobs.enablePromptActions"
    )
    disable_menu = next(
        item for item in title_menus if item["command"] == "djobs.disablePromptActions"
    )
    assert "!djobs.promptActionsEnabled" in enable_menu["when"]
    assert "djobs.promptActionsEnabled" in disable_menu["when"]

    task_menus = package["contributes"]["menus"]["view/item/context"]
    prompt_menus = [item for item in task_menus if item["command"] == "djobs.promptFinishWorkflow"]
    assert prompt_menus
    for item in prompt_menus:
        assert "djobs.promptActionsEnabled" in item["when"]

    forbidden_source_strings = [
        "openChatWithPrompt",
        "buildStartWorkflowPrompt",
        "buildResumePrompt",
        "buildResumePromptForCorrelation",
        "clipboard.writeText(prompt)",
        "autoTakeover",
        "Prompt copied",
        "prompt copied",
        "Start a tracked workflow",
    ]
    for source_file in _EXT_SOURCE_FILES:
        source = source_file.read_text(encoding="utf-8")
        for forbidden in forbidden_source_strings:
            relative_path = source_file.relative_to(_REPO)
            assert forbidden not in source, f"{relative_path} contains {forbidden}"


def test_extension_checks_marketplace_for_its_own_updates() -> None:
    source = (_REPO / "vscode-ext" / "src" / "extension.ts").read_text(encoding="utf-8")
    assert "maybeOfferMarketplaceExtensionUpdate" in source
    assert "latestMarketplaceVersion" in source
    assert "marketplace.visualstudio.com" in source
    assert "MARKETPLACE_CHECK_INTERVAL_MS" in source
    assert "workbench.extensions.search" in source


def test_extension_exposes_practical_task_controls() -> None:
    package = json.loads(_EXT_PACKAGE.read_text(encoding="utf-8"))
    task_menu_commands = {
        item["command"]
        for item in package["contributes"]["menus"]["view/item/context"]
        if "viewItem == task" in item["when"]
    }
    assert "djobs.archiveTask" in task_menu_commands
    assert "djobs.deleteTask" in task_menu_commands
    assert "djobs.viewTaskHistory" in task_menu_commands
    assert "djobs.inspectTask" in task_menu_commands
