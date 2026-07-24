from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXTRACTOR = runpy.run_path(str(ROOT / "scripts" / "extract_release_notes.py"))
extract_release_notes = cast(Any, EXTRACTOR["extract_release_notes"])


def test_extracts_only_requested_release_section() -> None:
    changelog = """# Changelog

## [Unreleased]

## [0.17.2] - 2026-07-24

### Fixed
- `[release]` Avoid fragile parsing.

## [0.17.1] - 2026-07-24

### Fixed
- `[release]` Earlier fix.
"""

    notes = extract_release_notes(changelog, "0.17.2")

    assert notes == "### Fixed\n- `[release]` Avoid fragile parsing.\n"


def test_rejects_missing_version_section() -> None:
    with pytest.raises(ValueError, match="no dated section for 0.17.2"):
        extract_release_notes("## [0.17.1] - 2026-07-24\n\n- old\n", "0.17.2")


def test_rejects_empty_version_section() -> None:
    changelog = """## [0.17.2] - 2026-07-24

## [0.17.1] - 2026-07-24

- old
"""

    with pytest.raises(ValueError, match="section for 0.17.2 is empty"):
        extract_release_notes(changelog, "0.17.2")
