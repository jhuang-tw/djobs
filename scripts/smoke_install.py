"""Smoke-test the built wheel in a clean, isolated virtual environment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

_TOP_LEVEL_HELP_MARKERS = (
    "Local repository memory",
    "djobs setup",
    "djobs doctor",
    "djobs memory list",
    "djobs legacy --help",
)


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


def _contract_smoke(
    *,
    root: Path,
    environment_root: Path,
    python: Path,
    djobs: Path,
    env: dict[str, str],
) -> None:
    contract = _venv_executable(environment_root, "djobs-contract")
    contract_mcp = _venv_executable(environment_root, "djobs-contract-mcp")
    if not contract.exists() or not contract_mcp.exists():
        raise AssertionError("installed wheel is missing advisory contract console scripts")

    git = shutil.which("git")
    if not git:
        raise AssertionError("git is required for the installed contract smoke test")
    contract_env = dict(env)
    contract_env["PATH"] = os.pathsep.join((str(python.parent), str(Path(git).parent)))
    # The public transport must remain ASCII-safe even when Python is explicitly using CP950.
    contract_env["PYTHONIOENCODING"] = "cp950"

    standalone = json.loads(
        _run(
            [str(contract), "--schema-major", "1", "capabilities"],
            env=contract_env,
            cwd=root,
        ).stdout
    )
    public = json.loads(
        _run(
            [str(djobs), "contract", "--schema-major", "1", "capabilities"],
            env=contract_env,
            cwd=root,
        ).stdout
    )
    for payload in (standalone, public):
        if not payload.get("ok") or payload.get("authority") != "advisory":
            raise AssertionError(f"unexpected contract capabilities: {payload}")
        if payload.get("side_effects") is not False or payload.get("writes_database") is not False:
            raise AssertionError(f"contract capabilities are not read-only: {payload}")
        operations = payload.get("operations", {})
        if operations.get("checkpoint", {}).get("available_in_advisory_mode") is not False:
            raise AssertionError("checkpoint was exposed in advisory mode")
        if operations.get("handoff", {}).get("available_in_advisory_mode") is not False:
            raise AssertionError("handoff was exposed in advisory mode")

    schema_probe = _run(
        [
            str(python),
            "-c",
            (
                "import json; from importlib.resources import files; "
                "root=files('djobs').joinpath('schemas/host_contract/v1'); "
                "names=('capabilities.schema.json','observation.schema.json',"
                "'receipt.schema.json'); "
                "print(json.dumps([json.loads(root.joinpath(n).read_text(encoding='utf-8'))"
                "['$schema'] for n in names]))"
            ),
        ],
        env=contract_env,
        cwd=root,
    )
    if len(json.loads(schema_probe.stdout)) != 3:
        raise AssertionError("installed wheel is missing host-contract JSON Schemas")

    workspace = root / "中文工作區-✅"
    workspace.mkdir()
    _run([git, "init", "-q", str(workspace)], env=contract_env, cwd=root)
    _run([git, "-C", str(workspace), "config", "user.name", "Contract Smoke"], env=contract_env, cwd=root)
    _run(
        [git, "-C", str(workspace), "config", "user.email", "smoke@example.invalid"],
        env=contract_env,
        cwd=root,
    )
    (workspace / "證據.txt").write_text("中文 observation ✅\n", encoding="utf-8")
    _run([git, "-C", str(workspace), "add", "."], env=contract_env, cwd=root)
    _run([git, "-C", str(workspace), "commit", "-qm", "unicode base"], env=contract_env, cwd=root)

    identity = json.loads(
        _run(
            [
                str(python),
                "-c",
                (
                    "import json,sys; from djobs.contract_repository import repository_state; "
                    "print(json.dumps(repository_state(sys.argv[1]), default=str))"
                ),
                str(workspace),
            ],
            env=contract_env,
            cwd=root,
        ).stdout
    )
    database = root / "contract-smoke.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE agent_observations (
            id TEXT PRIMARY KEY,
            correlation_id TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            session_id_hash TEXT,
            event_type TEXT NOT NULL,
            tool_name TEXT,
            summary TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    metadata = {
        "memory_status": "active",
        "repo_family_id": identity["fingerprint"],
        "checkout_id": identity["checkout_id"],
        "commit_sha": identity["head"],
        "feature_id": "feature-unicode",
        "task_id": "task-unicode",
        "command": "pytest 中文測試 ✅",
        "return_code": 1,
        "affected_files": ["證據.txt"],
    }
    connection.execute(
        """
        INSERT INTO agent_observations (
            id, correlation_id, agent_type, session_id_hash, event_type,
            tool_name, summary, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "unicode-failure",
            identity["fingerprint"],
            "smoke",
            "session-hash",
            "tool_failure",
            "pytest",
            "中文 failure observation ✅",
            json.dumps(metadata, ensure_ascii=False),
            "2026-08-05T10:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()
    before_bytes = database.read_bytes()
    before_mtime = database.stat().st_mtime_ns

    response_text = _run(
        [
            str(contract),
            "--schema-major",
            "1",
            "observation",
            "--cwd",
            str(workspace),
            "--db",
            str(database),
            "--repository-head",
            str(identity["head"]),
            "--repository-fingerprint",
            str(identity["fingerprint"]),
            "--feature-id",
            "feature-unicode",
            "--token-budget",
            "2000",
        ],
        env=contract_env,
        cwd=root,
    ).stdout
    response_text.encode("ascii")
    response = json.loads(response_text)
    observations = response.get("repository_observations", [])
    if not response.get("ok") or [item.get("id") for item in observations] != [
        "unicode-failure"
    ]:
        raise AssertionError(f"installed observation contract failed: {response}")
    if observations[0].get("summary") != "中文 failure observation ✅":
        raise AssertionError("Unicode observation did not round-trip through CP950-safe JSON")
    if database.read_bytes() != before_bytes or database.stat().st_mtime_ns != before_mtime:
        raise AssertionError("advisory observation query modified its SQLite evidence database")
    if database.with_name(database.name + "-wal").exists():
        raise AssertionError("read-only observation query created a WAL file")
    if database.with_name(database.name + "-shm").exists():
        raise AssertionError("read-only observation query created an SHM file")

    response_file = root / "observation-response.json"
    response_file.write_text(response_text, encoding="ascii")
    receipt = json.loads(
        _run(
            [
                str(contract),
                "--schema-major",
                "1",
                "receipt",
                "--response-file",
                str(response_file),
            ],
            env=contract_env,
            cwd=root,
        ).stdout
    )
    if receipt.get("valid") is not True:
        raise AssertionError(f"installed receipt verification failed: {receipt}")


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
        for expected in _TOP_LEVEL_HELP_MARKERS:
            if expected not in help_text:
                raise AssertionError(f"top-level help is missing {expected!r}")

        setup_help = _run([str(djobs), "setup", "--help"], env=env, cwd=workspace).stdout
        if "djobs setup setup" in setup_help or "djobs setup [-h]" not in setup_help:
            raise AssertionError(f"unexpected setup help:\n{setup_help}")

        _contract_smoke(
            root=root,
            environment_root=environment_root,
            python=python,
            djobs=djobs,
            env=env,
        )

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
