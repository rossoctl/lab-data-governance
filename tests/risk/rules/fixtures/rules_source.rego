package data_governance

triggered_rules contains "DG-001" if {
    input.event_type == "external_sharing"
    some _DG_001_item_0 in input.data_items
    every t in ["PII"] { t in _DG_001_item_0.regulatory_tags }
    some _DG_001_dest_0 in input.data_destinations
    every c in ["external"] { c in _DG_001_dest_0.data_destination_categories }
    _DG_001_dest_0.data_destination_trust_level == "UNTRUSTED_EXTERNAL"
}

triggered_rules contains "DG-002" if {
    input.event_type == "external_sharing"
    some _DG_002_item_0 in input.data_items
    every t in ["PHI"] { t in _DG_002_item_0.regulatory_tags }
    some _DG_002_dest_0 in input.data_destinations
    every c in ["external"] { c in _DG_002_dest_0.data_destination_categories }
    _DG_002_dest_0.data_destination_trust_level == "UNTRUSTED_EXTERNAL"
}

triggered_rules contains "DG-004" if {
    input.event_type == "external_sharing"
    some _DG_004_item_0 in input.data_items
    _DG_004_item_0.classification_level == "RESTRICTED"
    some _DG_004_dest_0 in input.data_destinations
    every c in ["external"] { c in _DG_004_dest_0.data_destination_categories }
    _DG_004_dest_0.data_destination_trust_level == "UNTRUSTED_EXTERNAL"
}

_rule_decisions := {"DG-001": {"risk_level": "critical", "enforcement_type": "block", "allowed_actions": ["redact"], "explanation": "PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk.", "confidence": 0.95}, "DG-002": {"risk_level": "critical", "enforcement_type": "block", "allowed_actions": [], "explanation": "PHI (Protected Health Information) detected in payload sent to UNTRUSTED_EXTERNAL destination. HIPAA violation.", "confidence": 0.95}, "DG-004": {"risk_level": "critical", "enforcement_type": "block", "allowed_actions": [], "explanation": "RESTRICTED document detected in external sharing event to UNTRUSTED_EXTERNAL destination. Sharing prohibited.", "confidence": 0.95}}
_rule_order := {"DG-001": 0, "DG-002": 1, "DG-004": 2}
_risk_level_rank := {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4, "unknown": 5}
_enforcement_rank := {"block": 0, "quarantine": 1, "require_approval": 2, "redact": 3, "mask": 4, "anonymize": 5, "encrypt": 6, "escalate": 7, "notify": 8, "warn": 9, "log_only": 10, "audit": 11, "allow": 12}

default policy_decision := {"risk_level": "none", "enforcement_type": "allow", "allowed_actions": [], "explanation": "No rules fired, falling back to default rule", "confidence": 1.0, "triggered_rules": ["0000"], "rule_combining_mode": "most_restrictive", "policy_version": "data-governance-v1:1.0.0"}

_UNRANKED := 9999

_risk_rank(id) := _risk_level_rank[_rule_decisions[id].risk_level]
_risk_rank(id) := _UNRANKED if { not _risk_level_rank[_rule_decisions[id].risk_level] }

_enf_rank(id) := _enforcement_rank[_rule_decisions[id].enforcement_type]
_enf_rank(id) := _UNRANKED if { not _enforcement_rank[_rule_decisions[id].enforcement_type] }

policy_decision := decision if {
    count(triggered_rules) > 0
    ids := sort([id | some id in triggered_rules])

    risk_winner := sort([[_risk_rank(id), id] | some id in ids])[0][1]
    enf_winner := sort([[_enf_rank(id), id] | some id in ids])[0][1]

    same := [risk_winner | risk_winner == enf_winner]
    chosen := array.concat(same, [id | some id in [risk_winner, enf_winner]; risk_winner != enf_winner])
    explanation := concat("; ", [_rule_decisions[id].explanation | some id in chosen])

    decision := {
        "risk_level": object.get(_rule_decisions[risk_winner], "risk_level", null),
        "enforcement_type": object.get(_rule_decisions[enf_winner], "enforcement_type", null),
        "allowed_actions": object.get(_rule_decisions[enf_winner], "allowed_actions", []),
        "explanation": explanation,
        "confidence": object.get(_rule_decisions[enf_winner], "confidence", null),
        "triggered_rules": ids,
        "rule_combining_mode": "most_restrictive",
        "policy_version": "data-governance-v1:1.0.0",
    }
}
