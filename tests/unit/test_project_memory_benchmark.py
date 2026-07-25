from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_project_memory.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_project_memory", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_payload_fixture_is_reproducible_and_explicitly_labeled() -> None:
    result = _MODULE.run(8)

    assert result["benchmark"] == "deterministic payload-size regression fixture"
    assert "Not provider billing" in result["disclaimer"]
    assert "modern agents" in result["disclaimer"]
    assert "proxy_reduction_percent" not in result
    assert result["serialized_payload_ratio"] < 1
    assert result["baseline"]["minimum_file_read_calls"] == 8
    assert result["djobs"]["mcp_calls"] == 1
    assert result["djobs"]["returned_memories"] >= 2
    assert result["djobs"]["estimated_tokens"] < result["baseline"]["estimated_tokens"]
