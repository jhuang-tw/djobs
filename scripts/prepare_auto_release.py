#!/usr/bin/env python3
"""Prepare one deterministic lockstep release from commits since the latest tag.

The script is intentionally dependency-free so GitHub Actions can run it before
installing the project. It updates every published version surface and promotes
unreleased commit subjects into a dated changelog section.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
INIT_PATH = ROOT / "src" / "djobs" / "__init__.py"
SERVER_PATH = ROOT / "server.json"
PACKAGE_PATH = ROOT / "vscode-ext" / "package.json"
LOCK_PATH = ROOT / "vscode-ext" / "package-lock.json"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"

Bump = Literal["patch", "minor", "major"]


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str


@dataclass(frozen=True)
class ReleasePlan:
    release: bool
    version: str
    tag: str
    previous_tag: str
    changed: bool
    bump: Bump | None
    commits: tuple[Commit, ...]


def _run(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        list(args),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "command failed: " + " ".join(args))
    return result.stdout.strip()


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise ValueError(f"invalid semantic version: {value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def format_version(value: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in value)


def bump_version(version: str, bump: Bump) -> str:
    major, minor, patch = parse_version(version)
    if bump == "major":
        return format_version((major + 1, 0, 0))
    if bump == "minor":
        return format_version((major, minor + 1, 0))
    return format_version((major, minor, patch + 1))


def current_version() -> str:
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        INIT_PATH.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise RuntimeError("could not read __version__")
    parse_version(match.group(1))
    return match.group(1)


def latest_version_tag() -> str:
    tags = _run("git", "tag", "--list", "v[0-9]*", "--sort=-v:refname").splitlines()
    for tag in tags:
        try:
            parse_version(tag.removeprefix("v"))
        except ValueError:
            continue
        return tag
    return ""


def tag_exists(tag: str) -> bool:
    return bool(_run("git", "tag", "--list", tag))


def select_base_version(working_version: str, latest_tag: str) -> str:
    """Return the greatest released or checked-in semantic version."""

    parse_version(working_version)
    if not latest_tag:
        return working_version
    tagged_version = latest_tag.removeprefix("v")
    parse_version(tagged_version)
    if parse_version(tagged_version) > parse_version(working_version):
        return tagged_version
    return working_version


def commits_since(tag: str) -> tuple[Commit, ...]:
    revision = f"{tag}..HEAD" if tag else "HEAD"
    raw = _run(
        "git",
        "log",
        revision,
        "--format=%H%x1f%s%x1f%b%x1e",
        "--reverse",
    )
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f", 2)
        if len(parts) != 3:
            continue
        commit = Commit(parts[0].strip(), parts[1].strip(), parts[2].strip())
        if commit.subject.casefold().startswith("chore(release):"):
            continue
        commits.append(commit)
    return tuple(commits)


def classify_bump(commits: Iterable[Commit]) -> Bump:
    selected: Bump = "patch"
    for commit in commits:
        subject = commit.subject
        body = commit.body
        if re.match(r"^[a-zA-Z]+(?:\([^)]*\))?!:", subject) or re.search(
            r"^BREAKING[ -]CHANGE:", body, re.MULTILINE | re.IGNORECASE
        ):
            return "major"
        if re.match(r"^feat(?:\([^)]*\))?:", subject, re.IGNORECASE):
            selected = "minor"
    return selected


def _clean_subject(subject: str) -> str:
    cleaned = re.sub(
        r"^(?:feat|fix|perf|refactor|revert|docs|test|build|ci|chore)(?:\([^)]*\))?!?:\s*",
        "",
        subject,
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        cleaned = subject.strip()
    return cleaned[0].upper() + cleaned[1:] if cleaned else "Repository update"


def _section_for(subject: str) -> str:
    if re.match(r"^feat(?:\([^)]*\))?!?:", subject, re.IGNORECASE):
        return "Added"
    if re.match(r"^fix(?:\([^)]*\))?!?:", subject, re.IGNORECASE):
        return "Fixed"
    if re.match(r"^perf(?:\([^)]*\))?!?:", subject, re.IGNORECASE):
        return "Performance"
    return "Changed"


def render_changelog_section(version: str, date: str, commits: Iterable[Commit]) -> str:
    grouped: dict[str, list[str]] = {}
    for commit in commits:
        grouped.setdefault(_section_for(commit.subject), []).append(_clean_subject(commit.subject))
    lines = [f"## [{version}] - {date}", ""]
    for heading in ("Added", "Changed", "Fixed", "Performance"):
        entries = grouped.get(heading)
        if not entries:
            continue
        lines.extend([f"### {heading}", *[f"- `[release]` {entry}" for entry in entries], ""])
    return "\n".join(lines).rstrip() + "\n"


def update_version_files(version: str) -> None:
    init_text = INIT_PATH.read_text(encoding="utf-8")
    init_text, count = re.subn(
        r'(^__version__\s*=\s*)["\'][^"\']+["\']',
        rf'\g<1>"{version}"',
        init_text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError("could not update __version__")
    INIT_PATH.write_text(init_text, encoding="utf-8")

    package = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
    package["version"] = version
    PACKAGE_PATH.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    lock["version"] = version
    lock.setdefault("packages", {}).setdefault("", {})["version"] = version
    LOCK_PATH.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    server = json.loads(SERVER_PATH.read_text(encoding="utf-8"))
    server["version"] = version
    for package_entry in server.get("packages", []):
        package_entry["version"] = version
    SERVER_PATH.write_text(json.dumps(server, indent=2) + "\n", encoding="utf-8")


def _changelog_base(working_version: str, previous_tag: str) -> str:
    if previous_tag:
        tagged_version = previous_tag.removeprefix("v")
        if parse_version(tagged_version) > parse_version(working_version):
            tagged_text = _run("git", "show", f"{previous_tag}:CHANGELOG.md")
            if "## [Unreleased]" not in tagged_text:
                raise RuntimeError(f"{previous_tag}:CHANGELOG.md has no [Unreleased] section")
            return tagged_text.rstrip() + "\n"
    return CHANGELOG_PATH.read_text(encoding="utf-8")


def update_changelog(
    version: str,
    date: str,
    commits: tuple[Commit, ...],
    *,
    working_version: str,
    previous_tag: str,
) -> None:
    text = _changelog_base(working_version, previous_tag)
    marker = "## [Unreleased]"
    if marker not in text:
        raise RuntimeError("CHANGELOG.md has no [Unreleased] section")
    if re.search(rf"^## \[{re.escape(version)}\] - ", text, re.MULTILINE):
        CHANGELOG_PATH.write_text(text, encoding="utf-8")
        return
    section = render_changelog_section(version, date, commits)
    replacement = marker + "\n\n" + section
    CHANGELOG_PATH.write_text(text.replace(marker, replacement, 1), encoding="utf-8")


def make_plan(date: str) -> ReleasePlan:
    working_version = current_version()
    latest_tag = latest_version_tag()
    current_tag = f"v{working_version}"

    # A protected-main release PR may already be merged while publishing is retried.
    if not tag_exists(current_tag) and latest_tag:
        latest_version = latest_tag.removeprefix("v")
        if parse_version(working_version) > parse_version(latest_version):
            return ReleasePlan(True, working_version, current_tag, latest_tag, False, None, ())

    base_version = select_base_version(working_version, latest_tag)
    commits = commits_since(latest_tag)
    if not commits:
        return ReleasePlan(
            False,
            base_version,
            f"v{base_version}",
            latest_tag,
            False,
            None,
            (),
        )

    bump = classify_bump(commits)
    next_version = bump_version(base_version, bump)
    update_version_files(next_version)
    update_changelog(
        next_version,
        date,
        commits,
        working_version=working_version,
        previous_tag=latest_tag,
    )
    return ReleasePlan(True, next_version, f"v{next_version}", latest_tag, True, bump, commits)


def write_outputs(path: str | None, plan: ReleasePlan) -> None:
    values = {
        "release": str(plan.release).lower(),
        "version": plan.version,
        "tag": plan.tag,
        "previous_tag": plan.previous_tag,
        "changed": str(plan.changed).lower(),
        "bump": plan.bump or "pending",
        "commit_count": str(len(plan.commits)),
    }
    if path:
        with Path(path).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    print(json.dumps(values, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="Release date in YYYY-MM-DD format",
    )
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        parser.error("--date must use YYYY-MM-DD")
    plan = make_plan(args.date)
    write_outputs(args.github_output, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
