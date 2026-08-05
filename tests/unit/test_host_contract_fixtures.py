from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from djobs import host_contract as contract


def test_golden_contract_fixtures_validate() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    schema_root = Path(contract.__file__).parent / "schemas" / "host_contract" / "v1"
    fixture_root = repository_root / "tests" / "fixtures" / "contract" / "v1"

    pairs = (
        ("capabilities.schema.json", "capabilities.json"),
        ("observation.schema.json", "observation.json"),
        ("receipt.schema.json", "receipt.json"),
    )
    for schema_name, fixture_name in pairs:
        schema = json.loads((schema_root / schema_name).read_text(encoding="utf-8"))
        fixture = json.loads((fixture_root / fixture_name).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(fixture)
