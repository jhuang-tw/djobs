"""Guards for the coding-focused MCP process surface."""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "djobs"


def test_coding_mcp_entrypoints_do_not_start_background_workers() -> None:
    entrypoints = (
        _SRC / "coding_mcp.py",
        _SRC / "mcp_server.py",
        _SRC / "low_token_mcp.py",
        _SRC / "delta_mcp.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in entrypoints)

    assert "_start_embedded_daemon" not in combined
    assert "threading.Thread" not in combined
    assert "BUILTIN_HANDLERS" not in combined
    assert "from djobs.daemon import" not in combined


def test_standalone_worker_runtime_remains_explicitly_available() -> None:
    assert (_SRC / "daemon.py").is_file()
    cli = (_SRC / "cli.py").read_text(encoding="utf-8")
    assert "djobs serve" in cli
