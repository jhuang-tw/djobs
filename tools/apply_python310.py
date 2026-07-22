from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def rewrite_datetime_utc(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if not re.search(r"\bUTC\b", text):
        return False

    import_pattern = re.compile(r"^from datetime import (?P<body>\([^)]*\)|[^\n]+)$", re.MULTILINE)

    def replace_import(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1]
        names = [item.strip() for item in body.replace("\n", " ").split(",") if item.strip()]
        if "UTC" not in names:
            return match.group(0)
        names = [name for name in names if name != "UTC"]
        if "timezone" not in names:
            names.append("timezone")
        return "from datetime import " + ", ".join(names)

    text = import_pattern.sub(replace_import, text)
    if re.search(r"from datetime import[^\n]*\bUTC\b", text):
        raise RuntimeError(f"unsupported datetime UTC import form in {path}")

    tokens: list[tokenize.TokenInfo] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.NAME and token.string == "UTC":
            token = tokenize.TokenInfo(
                type=token.type,
                string="timezone.utc",
                start=token.start,
                end=token.end,
                line=token.line,
            )
        tokens.append(token)
    rewritten = tokenize.untokenize(tokens)
    if rewritten != text:
        path.write_text(rewritten, encoding="utf-8")
        return True
    return False


def update_python_sources() -> None:
    roots = ("src", "tests", "examples", "scripts")
    changed = 0
    for root_name in roots:
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if rewrite_datetime_utc(path):
                changed += 1
    if changed == 0:
        raise RuntimeError("expected at least one datetime.UTC compatibility rewrite")


def update_pyproject() -> None:
    path = ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, 'requires-python = ">=3.11"', 'requires-python = ">=3.10"', "requires-python")
    text = replace_once(
        text,
        '    "Programming Language :: Python :: 3.11",',
        '    "Programming Language :: Python :: 3.10",\n    "Programming Language :: Python :: 3.11",',
        "Python 3.10 classifier",
    )
    text = replace_once(text, 'target-version = "py311"', 'target-version = "py310"', "Ruff target")
    text = replace_once(text, 'python_version = "3.11"', 'python_version = "3.10"', "mypy target")
    path.write_text(text, encoding="utf-8")


def update_ci() -> None:
    path = ROOT / ".github/workflows/ci.yml"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'python-version: ["3.11", "3.12", "3.13", "3.14"]',
        'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]',
        "CI Python matrix",
    )
    path.write_text(text, encoding="utf-8")


def update_public_surfaces() -> None:
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    readme = replace_once(
        readme,
        "[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)]",
        "[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)]",
        "README Python badge",
    )
    readme_path.write_text(readme, encoding="utf-8")

    contributing_path = ROOT / "CONTRIBUTING.md"
    contributing = contributing_path.read_text(encoding="utf-8")
    contributing = contributing.replace("Python 3.11+", "Python 3.10+")
    contributing = contributing.replace("Python 3.11 or newer", "Python 3.10 or newer")
    contributing_path.write_text(contributing, encoding="utf-8")

    install_path = ROOT / "install.bat"
    install = install_path.read_text(encoding="utf-8")
    install = install.replace("Python 3.11+", "Python 3.10+")
    install_path.write_text(install, encoding="utf-8")

    extension_path = ROOT / "vscode-ext/src/extension.ts"
    extension = extension_path.read_text(encoding="utf-8")
    extension = extension.replace("Python 3.11+", "Python 3.10+")
    extension = extension.replace("Python 3.11 or newer", "Python 3.10 or newer")
    extension_path.write_text(extension, encoding="utf-8")

    client_path = ROOT / "vscode-ext/src/djobsClient.ts"
    client = client_path.read_text(encoding="utf-8")
    client = client.replace("py -3.11+", "py -3.10+")
    client = client.replace("Python 3.11 or newer", "Python 3.10 or newer")
    client = client.replace("Python 3.11+", "Python 3.10+")
    client = client.replace("requires Python >=3.11", "requires Python >=3.10")
    client = replace_once(
        client,
        "          { kind: 'pip', exe: py, pyArgs: ['-3.11'], isVenv: false },\n"
        "          { kind: 'pip', exe: py, pyArgs: ['-3'], isVenv: false },",
        "          { kind: 'pip', exe: py, pyArgs: ['-3.11'], isVenv: false },\n"
        "          { kind: 'pip', exe: py, pyArgs: ['-3.10'], isVenv: false },\n"
        "          { kind: 'pip', exe: py, pyArgs: ['-3'], isVenv: false },",
        "Windows Python 3.10 installer fallback",
    )
    client_path.write_text(client, encoding="utf-8")


def update_changelog() -> None:
    path = ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    marker = (
        "### Compatibility\n"
        "- `[core]` The standalone `djobs serve` command, `Daemon`, `WorkerPool`, and handler APIs remain available for users who explicitly need general-purpose job execution; only automatic startup inside coding-agent MCP processes was removed.\n"
    )
    replacement = marker + (
        "- `[core]` **Python 3.10 support.** Lowered the runtime floor from Python 3.11 to 3.10, replaced 3.11-only `datetime.UTC` usage with `timezone.utc`, and added Python 3.10 to the tested CI matrix.\n"
    )
    text = replace_once(text, marker, replacement, "0.12.1 compatibility notes")
    path.write_text(text, encoding="utf-8")


def update_guard_tests() -> None:
    path = ROOT / "tests/unit/test_release_site_guards.py"
    text = path.read_text(encoding="utf-8")
    insertion = '''\n\ndef test_python_runtime_floor_is_310() -> None:\n    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")\n    workflow = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")\n\n    assert 'requires-python = ">=3.10"' in pyproject\n    assert '"Programming Language :: Python :: 3.10"' in pyproject\n    assert 'target-version = "py310"' in pyproject\n    assert 'python_version = "3.10"' in pyproject\n    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]' in workflow\n'''
    if "def test_python_runtime_floor_is_310" not in text:
        text += insertion
    path.write_text(text, encoding="utf-8")


def validate_no_runtime_utc_imports() -> None:
    for root_name in ("src", "tests"):
        for path in (ROOT / root_name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"from datetime import[^\n]*\bUTC\b", text):
                raise RuntimeError(f"Python 3.11-only datetime.UTC import remains in {path}")


def main() -> None:
    update_python_sources()
    update_pyproject()
    update_ci()
    update_public_surfaces()
    update_changelog()
    update_guard_tests()
    validate_no_runtime_utc_imports()


if __name__ == "__main__":
    main()
