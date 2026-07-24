from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("djobs_preflight", ROOT / "scripts/preflight.py")
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


def test_docs_only_change_uses_small_test_surface() -> None:
    changes = preflight.classify_changes(["README.md", "docs/index.html", "vscode-ext/README.md"])

    assert changes.docs_only is True
    assert changes.python is False
    assert changes.extension is False
    assert changes.unknown is False


def test_python_change_requires_python_validation() -> None:
    changes = preflight.classify_changes(["src/djobs/handoff.py", "tests/unit/test_handoff.py"])

    assert changes.python is True
    assert changes.docs_only is False
    assert changes.unknown is False


def test_dotfile_paths_keep_their_leading_dot() -> None:
    changes = preflight.classify_changes(["./.github/workflows/ci.yml", ".pre-commit-config.yaml"])

    assert changes.paths == (".github/workflows/ci.yml", ".pre-commit-config.yaml")
    assert changes.python is True
    assert changes.unknown is False


def test_extension_readme_does_not_compile_extension() -> None:
    changes = preflight.classify_changes(["vscode-ext/README.md"])

    assert changes.extension is False
    assert changes.docs_only is True


def test_extension_package_change_compiles_extension() -> None:
    changes = preflight.classify_changes(["vscode-ext/package.json"])

    assert changes.extension is True
    assert changes.docs_only is False


def test_unknown_change_is_conservative() -> None:
    changes = preflight.classify_changes(["custom-tooling/config.data"])

    assert changes.unknown is True
    assert changes.docs_only is False


def test_empty_change_set_is_conservative() -> None:
    changes = preflight.classify_changes([])

    assert changes.python is True
    assert changes.extension is True
    assert changes.unknown is True
