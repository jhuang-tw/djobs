from __future__ import annotations

import json
import re
from pathlib import Path

import djobs

ROOT = Path(__file__).resolve().parents[2]


def _json(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_every_published_version_surface_matches() -> None:
    version = djobs.__version__
    server = _json("server.json")
    package = _json("vscode-ext/package.json")
    lock = _json("vscode-ext/package-lock.json")

    assert server["version"] == version
    assert all(item["version"] == version for item in server["packages"])
    assert package["version"] == version
    assert lock["version"] == version
    assert lock["packages"][""]["version"] == version
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE
    )


def test_current_docs_and_marketplace_copy_match_passive_local_behavior() -> None:
    current_surfaces = [
        "README.md",
        "CONTRIBUTING.md",
        "AGENTS.md",
        "docs/RELEASE.md",
        "vscode-ext/README.md",
        "docs/index.html",
        "pyproject.toml",
        "server.json",
        "vscode-ext/package.json",
        "vscode-ext/src/extension.ts",
        "vscode-ext/src/djobsClient.ts",
    ]
    forbidden = [
        "automatic coding checkpoints",
        "smart command checkpoints",
        "smart coding hooks",
        "six coding-focused tools",
        "read-only task sidebar",
        "automatic checkpoint rewriting",
        "copilot cloud agent needs",
    ]
    combined = "\n".join(
        (ROOT / path).read_text(encoding="utf-8").lower() for path in current_surfaces
    )

    for phrase in forbidden:
        assert phrase not in combined
    assert "passive" in combined
    assert "explicit handoff" in combined
    assert "local" in combined
    assert "sync_workspace" in combined
    assert "checkpoint" in combined
    assert "handoff" in combined
    assert "resume_delta" in combined


def test_public_surfaces_share_layered_recovery_positioning() -> None:
    paths = ("README.md", "vscode-ext/README.md", "docs/index.html")
    surfaces = {path: (ROOT / path).read_text(encoding="utf-8").lower() for path in paths}
    required = (
        "local project memory and explicit handoff for ai coding agents.",
        "continue the repository instead of explaining it again in every new ai session.",
        'context_tier="resume"',
        "python 3.10",
        "node.js 20+",
        "1.101",
        "~224",
        "97.1%",
        "djobs gain",
        "sync_workspace",
        "memory",
        "checkpoint",
        "handoff",
        "resume_delta",
    )

    for path, text in surfaces.items():
        for marker in required:
            assert marker in text, f"{path} is missing shared public marker: {marker}"

    for path in ("README.md", "vscode-ext/README.md"):
        text = surfaces[path]
        assert "banner.png" not in text
        assert text.count("icon-128.png") == 1

    html = surfaces["docs/index.html"]
    icon_tag = (
        '<img src="https://raw.githubusercontent.com/jhuang-tw/djobs/main/'
        'vscode-ext/media/icon-128.png"'
    )
    assert "banner.png" not in html
    assert html.count(icon_tag) == 1


def test_version_sync_and_release_workflow_cover_every_published_surface() -> None:
    sync = (ROOT / "vscode-ext/scripts/sync-version.js").read_text(encoding="utf-8")
    planner = (ROOT / "scripts/prepare_auto_release.py").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    preflight = (ROOT / "scripts/preflight.py").read_text(encoding="utf-8")

    assert "package-lock.json" in sync
    assert "lock.packages[''].version" in sync
    assert 'lock.setdefault("packages", {}).setdefault("", {})["version"] = version' in planner
    assert 'server["version"] = version' in planner
    assert "select_base_version" in planner
    assert "push:\n    branches: [main]" in release
    assert "workflow_dispatch:" in ci
    assert "cancel-in-progress: true" in ci
    assert "scripts/preflight.py" in ci
    assert "automation/release-v" in ci
    assert "actions: write" in release
    assert "pull-requests: write" in release
    assert 'gh workflow run ci.yml --ref "$release_branch"' not in release
    assert "--event pull_request" in release
    assert 'if [ "$conclusion" = "action_required" ]; then' in release
    assert "/actions/runs/${pr_run_id}/approve" in release
    assert "gh pr create" in release
    assert "gh pr checks" in release
    assert "gh pr merge" in release
    assert "gh workflow run ci.yml --ref main" not in release
    assert "--event workflow_dispatch" not in release
    assert "scripts/prepare_auto_release.py" in release
    assert "scripts/extract_release_notes.py" in release
    assert "vscode-ext/package-lock.json" in release
    assert "automation/release-v" in release
    assert "gh release create" in release
    assert "HEAD:main" not in release
    assert ".github/release.json" not in release
    assert "profile" in preflight
    assert "ruff" in preflight
    assert not (ROOT / ".github/workflows/approve-release-pr-ci.yml").exists()
