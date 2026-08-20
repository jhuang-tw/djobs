from __future__ import annotations

import json
import sys

import pytest

from djobs import context_cli, contract_cli, public_cli


def test_contract_cli_rejects_unknown_major_as_fail_open(capsys) -> None:
    assert contract_cli.main(["--schema-major", "9", "capabilities"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["continue_workflow"] is True
    assert payload["error"]["code"] == "unsupported_schema_major"


def test_public_cli_routes_contract_without_entering_established_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[list[str]] = []

    def fake_contract(argv):
        called.append(list(argv))
        return 0

    monkeypatch.setattr(contract_cli, "main", fake_contract)
    monkeypatch.setattr(sys, "argv", ["djobs", "contract", "capabilities"])

    with pytest.raises(SystemExit) as exc:
        public_cli.main()

    assert exc.value.code == 0
    assert called == [["capabilities"]]


def test_public_cli_routes_context_without_entering_established_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[list[str]] = []

    def fake_context(argv):
        called.append(list(argv))
        return 0

    monkeypatch.setattr(context_cli, "main", fake_context)
    monkeypatch.setattr(sys, "argv", ["djobs", "context", "fix oauth"])

    with pytest.raises(SystemExit) as exc:
        public_cli.main()

    assert exc.value.code == 0
    assert called == [["fix oauth"]]
