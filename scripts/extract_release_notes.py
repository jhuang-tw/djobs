#!/usr/bin/env python3
"""Extract one version's release notes from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_PATH = ROOT / "CHANGELOG.md"


def extract_release_notes(text: str, version: str) -> str:
    heading = re.compile(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
        re.MULTILINE,
    )
    match = heading.search(text)
    if match is None:
        raise ValueError(f"CHANGELOG.md has no dated section for {version}")

    body_start = match.end()
    next_section = re.search(r"^## \[", text[body_start:], re.MULTILINE)
    body_end = body_start + next_section.start() if next_section else len(text)
    body = text[body_start:body_end].strip()
    if not body:
        raise ValueError(f"CHANGELOG.md section for {version} is empty")
    return body + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("output", type=Path)
    parser.add_argument("--changelog", type=Path, default=CHANGELOG_PATH)
    args = parser.parse_args(argv)

    notes = extract_release_notes(
        args.changelog.read_text(encoding="utf-8"),
        args.version,
    )
    args.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
