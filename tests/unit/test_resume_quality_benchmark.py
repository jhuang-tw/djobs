from __future__ import annotations

from scripts.benchmark_resume_quality import run


def test_resume_quality_benchmark_passes() -> None:
    result = run()

    assert result["pass"] is True
    assert result["recall_at_3"] == 1.0
    assert result["stale_memory_injection_rate"] == 0.0
    assert result["deterministic_selection"] is True
    assert result["explainable_context_items"] == result["selected_context_items"]
