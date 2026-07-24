from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
RUFF_TARGETS = (
    "src",
    "tests",
    "scripts/prepare_auto_release.py",
    "scripts/extract_release_notes.py",
    "scripts/preflight.py",
)
DOC_TESTS = (
    "tests/unit/test_release_surfaces.py",
    "tests/unit/test_release_site_guards.py",
)


@dataclass(frozen=True)
class ChangeSet:
    paths: tuple[str, ...]
    python: bool
    extension: bool
    docs_only: bool
    unknown: bool


def _normalized(paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({path.replace("\\", "/").lstrip("./") for path in paths if path}))


def classify_changes(paths: Iterable[str]) -> ChangeSet:
    normalized = _normalized(paths)
    if not normalized or "*" in normalized:
        return ChangeSet(normalized, python=True, extension=True, docs_only=False, unknown=True)

    python = any(
        path.startswith(("src/", "tests/", "migrations/", "scripts/"))
        or path in {"pyproject.toml", ".pre-commit-config.yaml"}
        or path.startswith(".github/workflows/")
        for path in normalized
    )
    extension = any(
        path.startswith("vscode-ext/")
        and path
        not in {
            "vscode-ext/README.md",
            "vscode-ext/media/icon-128.png",
            "vscode-ext/media/banner.png",
        }
        for path in normalized
    )
    docs_only = all(
        path.endswith((".md", ".html", ".svg", ".png"))
        or path.startswith("docs/")
        for path in normalized
    )
    known = all(
        path.startswith(
            (
                "src/",
                "tests/",
                "migrations/",
                "scripts/",
                "docs/",
                "vscode-ext/",
                ".github/",
            )
        )
        or path
        in {
            "README.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "AGENTS.md",
            "LICENSE",
            "pyproject.toml",
            "server.json",
            ".pre-commit-config.yaml",
            ".gitignore",
        }
        for path in normalized
    )
    return ChangeSet(normalized, python=python, extension=extension, docs_only=docs_only, unknown=not known)


def _run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    rendered = " ".join(command)
    print(f"\n==> {rendered}", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def changed_files(base_ref: str | None) -> tuple[str, ...]:
    if base_ref:
        try:
            merge_base = _git_output("merge-base", base_ref, "HEAD")
            return _normalized(_git_output("diff", "--name-only", f"{merge_base}..HEAD").splitlines())
        except subprocess.CalledProcessError:
            print(f"Could not resolve {base_ref}; using conservative full preflight.", file=sys.stderr)
            return ("*",)

    names: set[str] = set()
    for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only")):
        output = _git_output(*args)
        names.update(output.splitlines())
    untracked = _git_output("ls-files", "--others", "--exclude-standard")
    names.update(untracked.splitlines())
    return _normalized(names) or ("*",)


def _ruff_python_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for directory in (ROOT / "src", ROOT / "tests"):
        files.extend(path for path in directory.rglob("*.py") if path.is_file())
    for relative in RUFF_TARGETS[2:]:
        path = ROOT / relative
        if path.is_file():
            files.append(path)
    return tuple(sorted(set(files)))


def _snapshot(paths: Iterable[Path]) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in paths}


def run_format_and_lint(*, fix: bool) -> None:
    tracked = _ruff_python_files()
    before = _snapshot(tracked) if fix else {}
    format_command = [sys.executable, "-m", "ruff", "format"]
    if not fix:
        format_command.append("--check")
    format_command.extend(RUFF_TARGETS)
    _run(format_command)

    if fix:
        changed = [path for path in tracked if before.get(path) != path.read_bytes()]
        if changed:
            print("\nRuff formatted files. Review/stage them, then rerun preflight:", file=sys.stderr)
            for path in changed:
                print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
            raise SystemExit(2)

    _run([sys.executable, "-m", "ruff", "check", *RUFF_TARGETS])


def run_typecheck() -> None:
    _run([sys.executable, "-m", "mypy"])


def run_tests(changes: ChangeSet, *, full: bool) -> None:
    if full or changes.python or changes.unknown:
        targets: tuple[str, ...] = ()
    else:
        targets = DOC_TESTS
    _run([sys.executable, "-m", "pytest", "-q", "--tb=short", *targets])


def run_extension() -> None:
    if shutil.which("npm") is None:
        raise SystemExit("npm is required because VS Code extension inputs changed.")
    _run(["npm", "ci"], cwd=ROOT / "vscode-ext")
    _run(["npx", "tsc", "-p", "./", "--noEmit"], cwd=ROOT / "vscode-ext")
    _run(["npm", "run", "compile"], cwd=ROOT / "vscode-ext")


def run_package_checks() -> None:
    _run([sys.executable, "-m", "build"])
    distributions = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "dist").glob("*"))
    if not distributions:
        raise SystemExit("python -m build produced no distributions")
    _run([sys.executable, "-m", "twine", "check", *distributions])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the shared djobs pre-push and CI gates.")
    parser.add_argument("--profile", choices=("lint", "quick", "full"), default="quick")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fix", action="store_true", help="Apply Ruff formatting and stop if files change.")
    mode.add_argument("--check", action="store_true", help="Check formatting without modifying files.")
    parser.add_argument("--base-ref", help="Git ref used to classify changed files.")
    parser.add_argument("--changed-file", action="append", default=[], help="Explicit changed path for tests or automation.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    changes = classify_changes(args.changed_file or changed_files(args.base_ref))
    print("Changed paths:")
    for path in changes.paths:
        print(f"  - {path}")

    run_format_and_lint(fix=args.fix)
    if args.profile == "lint":
        return 0

    run_typecheck()
    run_tests(changes, full=args.profile == "full")
    if args.profile == "full" or changes.extension:
        run_extension()
    if args.profile == "full":
        run_package_checks()

    print("\nPreflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
