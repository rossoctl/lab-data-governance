"""The live tier's test catalog (``tests/live/catalog_e2e.json``) is the
shipped catalog plus ``E2E-*`` rules and nothing else: every shipped rule
byte-identical, the envelope untouched except for its version, the whole
document schema-valid and compilable through the production path
(``compile.compile_bundle(path)``). Runs in the default pass — the live
tier itself does not, so this is what keeps the catalog honest between
live runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_governance.risk.rules import catalog, compile

_SHIPPED = Path(catalog.__file__).parent / "_policy_data" / "rules_source.json"
_LIVE_CATALOG = Path(__file__).parents[2] / "live" / "catalog_e2e.json"

E2E_RULES = ["E2E-PI-INT", "E2E-INT-DATA", "E2E-CRED-INT", "E2E-CRED-EXT", "E2E-INVERT"]


def test_live_catalog_compiles_and_extends_the_shipped_rules() -> None:
    """The live tier's catalog is the shipped catalog plus E2E-* rules:
    every shipped rule byte-identical, the envelope untouched except for
    the version, and the whole thing schema-valid."""
    shipped = json.loads(_SHIPPED.read_text(encoding="utf-8"))
    live = json.loads(_LIVE_CATALOG.read_text(encoding="utf-8"))
    assert live["version"] == "1.0.0-e2e"
    for key in shipped:
        if key not in ("rules", "version"):
            assert live[key] == shipped[key], f"envelope key {key} changed"
    shipped_ids = [r["rule_id"] for r in shipped["rules"]]
    live_by_id = {r["rule_id"]: r for r in live["rules"]}
    for r in shipped["rules"]:
        assert live_by_id[r["rule_id"]] == r, f"{r['rule_id']} differs from the shipped rule"
    extra = [i for i in live_by_id if i not in shipped_ids]
    assert extra == E2E_RULES
    rego = compile.compile_bundle(_LIVE_CATALOG)
    for rule_id in extra:
        assert rule_id in rego
