from __future__ import annotations

import json

from djobs import context_cli


class _Client:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def sync_workspace(self, **kwargs):
        self.calls.append(dict(kwargs))
        return json.dumps(self.payload)


def test_context_preview_renders_resume_without_claiming(monkeypatch, capsys) -> None:
    client = _Client(
        {
            "ok": True,
            "workspace": "repo",
            "resume": {
                "goal": "Fix OAuth callback",
                "constraints": ["Keep public API"],
                "progress": ["Parser updated"],
                "failures": ["Do not strip +"],
                "next": "Run focused test",
            },
            "tasks": [],
            "next_step": "Review recent repository observations.",
        }
    )
    monkeypatch.setattr(
        context_cli.ProjectMemory,
        "open",
        classmethod(lambda cls, **kwargs: client),
    )

    assert context_cli.main(["OAuth callback"]) == 0
    output = capsys.readouterr().out
    assert "Goal: Fix OAuth callback" in output
    assert "Do not strip +" in output
    assert client.calls == [
        {
            "query": "OAuth callback",
            "context_tier": "resume",
            "token_budget": 500,
            "max_items": 6,
        }
    ]


def test_context_preview_json_preserves_payload(monkeypatch, capsys) -> None:
    client = _Client({"ok": True, "workspace": "repo", "state": "empty"})
    monkeypatch.setattr(
        context_cli.ProjectMemory,
        "open",
        classmethod(lambda cls, **kwargs: client),
    )

    assert context_cli.main(["--json", "--tier", "evidence"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "workspace": "repo", "state": "empty"}
    assert client.calls[0]["context_tier"] == "evidence"


def test_context_preview_returns_failure_for_fail_open_payload(monkeypatch, capsys) -> None:
    client = _Client({"ok": False, "continue_coding": True, "error": "storage unavailable"})
    monkeypatch.setattr(
        context_cli.ProjectMemory,
        "open",
        classmethod(lambda cls, **kwargs: client),
    )

    assert context_cli.main([]) == 1
    assert "storage unavailable" in capsys.readouterr().out
