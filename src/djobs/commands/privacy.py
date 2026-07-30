"""CLI for local secret-redaction verification without exposing credentials."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from djobs.privacy import redact

_MAX_FILES = 5000
_MAX_FILE_BYTES = 1024 * 1024
_EXCLUDED_PARTS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}
_SENSITIVE_PATTERNS = (".env", ".env.*", ".npmrc", ".pypirc", "*.pem", "*.key")


def _candidate_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    files: dict[str, Path] = {}
    if result is not None and result.returncode == 0:
        for item in result.stdout.split(b"\0"):
            if not item:
                continue
            relative = Path(item.decode("utf-8", errors="surrogateescape"))
            files[str(relative)] = root / relative
    else:
        for path in root.rglob("*"):
            if len(files) >= _MAX_FILES:
                break
            if path.is_file() and not _EXCLUDED_PARTS.intersection(path.parts):
                files[str(path.relative_to(root))] = path

    # Secret-bearing files are commonly ignored by Git. Include only bounded,
    # explicit credential/config patterns instead of recursively scanning every
    # ignored build artifact.
    for pattern in _SENSITIVE_PATTERNS:
        for path in root.rglob(pattern):
            if len(files) >= _MAX_FILES:
                break
            if path.is_file() and not _EXCLUDED_PARTS.intersection(path.parts):
                files.setdefault(str(path.relative_to(root)), path)
    return [files[key] for key in sorted(files)[:_MAX_FILES]]


def scan(root: str | Path) -> dict[str, Any]:
    """Scan bounded local text files and report only non-secret categories/counts."""

    base = Path(root).expanduser().resolve()
    category_counts: Counter[str] = Counter()
    findings: list[dict[str, Any]] = []
    scanned = 0
    skipped = 0
    for path in _candidate_files(base):
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                skipped += 1
                continue
            raw = path.read_bytes()
            if b"\0" in raw:
                skipped += 1
                continue
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        scanned += 1
        result = redact(text)
        if not result.redaction_count:
            continue
        category_counts.update(result.categories)
        findings.append(
            {
                "path": str(path.relative_to(base)),
                "redaction_count": result.redaction_count,
                "categories": list(result.categories),
            }
        )
    return {
        "ok": True,
        "root": str(base),
        "scanned_files": scanned,
        "skipped_files": skipped,
        "files_with_findings": len(findings),
        "category_counts": dict(sorted(category_counts.items())),
        "findings": findings,
        "secrets_returned": False,
    }


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="djobs privacy",
        description="Verify local redaction rules without returning original secrets.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    scan_parser = subparsers.add_parser("scan", help="Scan bounded tracked text files")
    scan_parser.add_argument("path", nargs="?", default=".")
    scan_parser.add_argument("--json", action="store_true", dest="as_json")
    test_parser = subparsers.add_parser(
        "test-redaction", help="Show how one supplied fixture is redacted"
    )
    test_parser.add_argument("fixture")
    test_parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.action == "scan":
        payload = scan(args.path)
    else:
        result = redact(args.fixture)
        payload = {
            "ok": True,
            "redacted": result.text,
            "categories": list(result.categories),
            "redaction_count": result.redaction_count,
        }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.action == "scan":
        print(
            f"Scanned {payload['scanned_files']} files; "
            f"{payload['files_with_findings']} file(s) contain redaction candidates."
        )
        for item in payload["findings"]:
            print(f"- {item['path']}: {', '.join(item['categories'])}")
    else:
        print(payload["redacted"])
        print("categories: " + ", ".join(payload["categories"]))
    return 0
