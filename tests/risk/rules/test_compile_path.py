"""``compile.compile_bundle(path)``: the one sanctioned way to put a
catalog other than the shipped one in front of OPA (the live tier's
``tests/live/catalog_e2e.json`` through ``deploy/create-opa-configmap.sh``).

The path form must be the same compiler and the same schema check as the
production default — a catalog that would be rejected as the shipped file
must be rejected here too — and the shipped file through the path form
must produce byte-for-byte the bundle the default produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from data_governance.risk.rules import catalog, compile

_SHIPPED = Path(catalog.__file__).parent / "_policy_data" / "rules_source.json"


def test_shipped_path_equals_default() -> None:
    assert compile.compile_bundle(_SHIPPED) == compile.compile_bundle()


def test_path_form_is_not_memoized(tmp_path: Path) -> None:
    policy = json.loads(_SHIPPED.read_text(encoding="utf-8"))
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(policy), encoding="utf-8")
    policy["rules"] = policy["rules"][:1]
    b.write_text(json.dumps(policy), encoding="utf-8")
    assert compile.compile_bundle(a) != compile.compile_bundle(b)
    assert "DG-004" in compile.compile_bundle(a)
    assert "DG-004" not in compile.compile_bundle(b)


def test_schema_invalid_catalog_is_rejected(tmp_path: Path) -> None:
    policy = json.loads(_SHIPPED.read_text(encoding="utf-8"))
    policy["rules"][0]["rule_decision"]["risk_level"] = "bogus"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(jsonschema.ValidationError):
        compile.compile_bundle(bad)


def test_missing_catalog_raises() -> None:
    with pytest.raises(FileNotFoundError):
        compile.compile_bundle("/nonexistent/catalog.json")
