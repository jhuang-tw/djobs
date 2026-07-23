"""Smoke-test the built wheel in a clean, isolated virtual environment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def _venv_executable(root: Path, name: str) -> Path:
    scripts = root / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    return scripts / f"{name}{suffix}"


def _run(
    command: list[str], *, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _find_wheel(path: Path) -> Path:
    if path.is_file():
        return path
    wheels = sorted(path.glob("djobs-*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected exactly one djobs wheel in {path}, found {len(wheels)}")
    return wheels[0]


def smoke(wheel: Path, *, find_links: Path | None = None) -> None:
    wheel = _find_wheel(wheel).resolve()
    with tempfile.TemporaryDirectory(prefix="djobs-installed-smoke-") as raw_root:
        root = Path(raw_root)
        environment_root = root / "venv"
        home = root / "home"
        workspace = root / "workspace"
        home.mkdir()
        workspace.mkdir()
        venv.EnvBuilder(with_pip=True, clear=True).create(environment_root)

        python = _venv_executable(environment_root, "python")
        djobs = _venv_executable(environment_root, "djobs")
        scripts = python.parent
        install = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
        if find_links is not None:
            install.extend(["--no-index", "--find-links", str(find_links.resolve())])
        install.append(str(wheel))
        _run(install, env=os.environ.copy(), cwd=workspace)

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "DJOBS_DB": str(home / ".djobs" / "smoke.db"),
                "DJOBS_AGENT_TYPE": "copilot",
                # Keep the test independent from any agent CLIs installed on the runner.
                "PATH": str(scripts),
            }
        )

        version = _run([str(djobs), "--version"], env=env, cwd=workspace).stdout.strip()
        if not version.startswith("djobs "):
            raise AssertionError(f"unexpected version output: {version!r}")

        help_text = _run([str(djobs), "--help"], env=env, cwd=workspace).stdout
        for expected in ("Local repository memory", "djobs setup", "djobs repair", "djobs remove"):
            if expected not in help_text:
                raise AssertionError(f"top-level help is missing {expected!r}")

        setup_help = _run([str(djobs), "setup", "--help"], env=env, cwd=workspace).stdout
        if "djobs setup setup" in setup_help or "djobs setup [-h]" not in setup_help:
            raise AssertionError(f"unexpected setup help:\n{setup_help}")

        # A registry/host-installed MCP must become usable on its first tool call,
        # before the user knows or runs any djobs setup command.
        first_call = _run(
            [
                str(python),
                "-c",
                (
                    "import asyncio,json; "
                    "from types import SimpleNamespace; "
                    "from djobs import coding_mcp; "
                    "S=type('S',(),{'client_params':SimpleNamespace("
                    "clientInfo=SimpleNamespace(name='Visual Studio Code',title=None)),"
                    "'list_roots':lambda self: asyncio.sleep("
                    "0,result=SimpleNamespace(roots=[]))}); "
                    "c=SimpleNamespace(session=S(),request_context=None,client_id=None); "
                    "print(asyncio.run(coding_mcp.sync_workspace(c)))"
                ),
            ],
            env=env,
            cwd=workspace,
        )
        first_payload = json.loads(first_call.stdout)
        if not first_payload.get("ok"):
            raise AssertionError(f"first MCP call failed: {first_payload}")

        hook_path = home / ".copilot" / "hooks" / "djobs.json"
        if not Path(env["DJOBS_DB"]).exists():
            raise AssertionError("first MCP call did not create local SQLite memory")
        if not hook_path.exists():
            raise AssertionError("first MCP call did not install passive Copilot hooks")
        if "djobs.hook_entrypoint" not in hook_path.read_text(encoding="utf-8"):
            raise AssertionError("first MCP call installed an invalid passive hook document")

        # Manual setup remains an idempotent repair path, not required onboarding.
        _run([str(djobs), "setup", "copilot"], env=env, cwd=workspace)
        first_hook = hook_path.read_text(encoding="utf-8")
        hook_document = json.loads(first_hook)
        if "SessionStart" not in hook_document.get("hooks", {}):
            raise AssertionError("Copilot passive hook was not installed")

        # Setup and repair must be safe to repeat without accumulating or corrupting config.
        _run([str(djobs), "setup", "copilot"], env=env, cwd=workspace)
        _run([str(djobs), "repair", "copilot"], env=env, cwd=workspace)
        json.loads(hook_path.read_text(encoding="utf-8"))

        doctor = _run([str(djobs), "doctor", "--json"], env=env, cwd=workspace)
        report = json.loads(doctor.stdout)
        package_checks = [
            item for item in report.get("checks", []) if item.get("name") == "djobs package"
        ]
        if not package_checks or not package_checks[0].get("ok"):
            raise AssertionError(f"installed package failed doctor check: {report}")

        _run([str(djobs), "remove", "copilot"], env=env, cwd=workspace)
        if hook_path.exists() and "djobs.hook_entrypoint" in hook_path.read_text(encoding="utf-8"):
            raise AssertionError("djobs-managed Copilot hooks remained after remove")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True, help="Wheel file or directory")
    parser.add_argument(
        "--find-links",
        type=Path,
        default=None,
        help="Optional offline wheelhouse used while installing dependencies",
    )
    args = parser.parse_args()
    smoke(args.wheel, find_links=args.find_links)
    print("Installed wheel smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
