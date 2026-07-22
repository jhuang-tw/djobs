from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.12.1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def update_mcp_server() -> None:
    path = ROOT / "src/djobs/mcp_server.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "The server embeds a lightweight background daemon (WorkerPool + SchedulerLoop)\n"
        "that auto-starts when the MCP process launches.  Registered built-in handlers\n"
        "(e.g. ``echo``) are executed automatically; AI-powered tasks are handled by\n"
        "the Copilot agent itself via the normal tool-call flow.\n",
        "The MCP process is passive: it stores and retrieves durable coding state but\n"
        "does not start workers, schedulers, or polling threads. Agents perform coding\n"
        "work through the normal tool-call flow; ``djobs serve`` remains available for\n"
        "users who explicitly need the standalone general-purpose worker runtime.\n",
        "mcp module description",
    )
    for line in ("import atexit\n", "import logging\n", "import threading\n"):
        text = replace_once(text, line, "", f"remove {line.strip()}")
    text = replace_once(
        text,
        "from djobs.cli import BUILTIN_HANDLERS, build_work_receipt\n",
        "from djobs.cli import build_work_receipt\n",
        "remove builtin handler import",
    )
    text = replace_once(text, "from djobs.daemon import Daemon\n", "", "remove daemon import")
    text = replace_once(text, "logger = logging.getLogger(__name__)\n\n", "", "remove logger")
    text = replace_once(
        text,
        "_daemon: Daemon | None = None\n_daemon_thread: threading.Thread | None = None\n",
        "",
        "remove daemon state",
    )
    start = text.index("def _start_embedded_daemon() -> None:")
    end = text.index("def _dumps(obj: Any) -> str:", start)
    text = text[:start] + text[end:]
    text = replace_once(
        text,
        "def main() -> None:\n"
        "    \"\"\"Run the MCP server over stdio (used by VS Code).\n\n"
        "    Also starts the embedded background daemon so built-in handlers\n"
        "    are processed automatically — zero user setup required.\n"
        "    \"\"\"\n"
        "    _get_queue()  # ensure db is initialised\n"
        "    _start_embedded_daemon()  # background worker for built-in handlers\n"
        "    _server.run(transport=\"stdio\")\n",
        "def main() -> None:\n"
        "    \"\"\"Run the passive MCP server over stdio (used by coding agents).\"\"\"\n\n"
        "    _get_queue()  # ensure db is initialised\n"
        "    _server.run(transport=\"stdio\")\n",
        "passive mcp main",
    )
    path.write_text(text, encoding="utf-8")


def update_low_token_entrypoint() -> None:
    path = ROOT / "src/djobs/low_token_mcp.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "    _start_embedded_daemon,\n", "", "low-token daemon import")
    text = replace_once(text, "    _start_embedded_daemon()\n", "", "low-token daemon call")
    path.write_text(text, encoding="utf-8")


def update_delta_entrypoint() -> None:
    path = ROOT / "src/djobs/delta_mcp.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "    _start_embedded_daemon,\n", "", "delta daemon import")
    text = replace_once(text, "    _start_embedded_daemon()\n", "", "delta daemon call")
    path.write_text(text, encoding="utf-8")


def update_versions() -> None:
    init_path = ROOT / "src/djobs/__init__.py"
    init_text = init_path.read_text(encoding="utf-8")
    init_text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{VERSION}"', init_text, count=1)
    init_path.write_text(init_text, encoding="utf-8")

    server_path = ROOT / "server.json"
    server = json.loads(server_path.read_text(encoding="utf-8"))
    server["version"] = VERSION
    for package in server.get("packages", []):
        package["version"] = VERSION
    server_path.write_text(json.dumps(server, indent=2) + "\n", encoding="utf-8")

    package_path = ROOT / "vscode-ext/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["version"] = VERSION
    package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")

    lock_path = ROOT / "vscode-ext/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["version"] = VERSION
    lock["packages"][""]["version"] = VERSION
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def update_changelog() -> None:
    path = ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    section = """## [0.12.1] - 2026-07-22

### Changed
- `[core]` **Passive coding MCP.** The normal, low-token, and delta-context MCP entry points now initialize only the durable queue and stdio server. They no longer start a worker pool or scheduler behind the coding agent.

### Removed
- `[core]` **Implicit background polling.** Removed the embedded daemon thread, its two-second worker polling, five-second scheduler polling, built-in handler registration, and process-exit thread cleanup from MCP startup.

### Compatibility
- `[core]` The standalone `djobs serve` command, `Daemon`, `WorkerPool`, and handler APIs remain available for users who explicitly need general-purpose job execution; only automatic startup inside coding-agent MCP processes was removed.

"""
    text = replace_once(text, "## [Unreleased]\n\n", "## [Unreleased]\n\n" + section, "changelog")
    path.write_text(text, encoding="utf-8")


def add_guard_test() -> None:
    path = ROOT / "tests/unit/test_coding_mcp_surface.py"
    path.write_text(
        '''"""Guards for the coding-focused MCP process surface."""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "djobs"


def test_coding_mcp_entrypoints_do_not_start_background_workers() -> None:
    entrypoints = (
        _SRC / "mcp_server.py",
        _SRC / "low_token_mcp.py",
        _SRC / "delta_mcp.py",
    )
    combined = "\\n".join(path.read_text(encoding="utf-8") for path in entrypoints)

    assert "_start_embedded_daemon" not in combined
    assert "threading.Thread" not in combined
    assert "BUILTIN_HANDLERS" not in combined
    assert "from djobs.daemon import" not in combined


def test_standalone_worker_runtime_remains_explicitly_available() -> None:
    assert (_SRC / "daemon.py").is_file()
    cli = (_SRC / "cli.py").read_text(encoding="utf-8")
    assert "djobs serve" in cli
''',
        encoding="utf-8",
    )


def main() -> None:
    update_mcp_server()
    update_low_token_entrypoint()
    update_delta_entrypoint()
    update_versions()
    update_changelog()
    add_guard_test()


if __name__ == "__main__":
    main()
