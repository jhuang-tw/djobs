from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, minimum: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"{path}: expected at least {minimum} matches for {old!r}, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    replace(
        "src/djobs/cli.py",
        "stable idempotency key",
        "stable `idempotency_key`",
    )
    replace(
        "tests/unit/test_entrypoint.py",
        "from djobs import delta_mcp, mcp_server",
        "from djobs import coding_mcp, mcp_server",
    )
    replace(
        "tests/unit/test_entrypoint.py",
        'monkeypatch.setattr(delta_mcp, "main", lambda: calls.append(("run", None)))',
        'monkeypatch.setattr(coding_mcp, "main", lambda: calls.append(("run", None)))',
    )
    replace(
        "tests/unit/test_install_instructions.py",
        'assert "durable task queue" in out',
        'assert "coding checkpoints" in out',
    )
    replace(
        "tests/unit/test_install_mcp_command.py",
        "djobs.mcp_server",
        "djobs.coding_mcp",
        minimum=4,
    )
    replace(
        "tests/unit/test_release_site_guards.py",
        'assert "djobs.delta_mcp" in client_text',
        'assert "djobs.coding_mcp" in client_text',
    )


if __name__ == "__main__":
    main()
