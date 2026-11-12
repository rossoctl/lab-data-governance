"""The one rule of the system tests.

T-SENS: a payload sent to an external party that carries a sensitive label
(regulatory tag ``PII``), whether the classifier found it in this payload or
label lineage carried it there, is blocked. The rule does not care where the
label came from; the on/off pair of every case separates the two mechanisms.

The shipped catalogue (DG-001, DG-002, DG-004) is replaced in the harness by
this rule: the policy document keeps the shipped enums and combining mode and
swaps the ``rules`` list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_governance.risk.rules import catalog

SHIPPED = Path(catalog.__file__).parent / "_policy_data" / "rules_source.json"

T_SENS: dict[str, Any] = {
    "rule_id": "T-SENS",
    "rule_name": "sensitive_label_to_external",
    "rule_categories": ["data_exfiltration"],
    "rule_sources": [],
    "event_type": "external_sharing",
    "data_items": [{"regulatory_tags": ["PII"]}],
    "data_destinations": [
        {"data_destination_categories": ["external"], "data_destination_trust_level": "UNTRUSTED_EXTERNAL"}
    ],
    "rule_decision": {
        "risk_level": "critical",
        "enforcement_type": "block",
        "allowed_actions": [],
        "explanation": "A sensitive label (own or inherited) reaches an external party.",
        "confidence": 1.0,
    },
}


def policy() -> dict[str, Any]:
    doc = json.loads(SHIPPED.read_text(encoding="utf-8"))
    doc["policy_id"] = "system-tests-t-sens"
    doc["rules"] = [T_SENS]
    return doc
