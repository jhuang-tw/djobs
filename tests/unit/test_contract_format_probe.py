from __future__ import annotations

import subprocess
import sys


def test_show_canonical_ruff_format_diff() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--diff",
            "src/djobs/contract_repository.py",
            "src/djobs/host_contract.py",
            "tests/unit/test_host_contract.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
