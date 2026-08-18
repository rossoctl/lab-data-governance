package data_governance

triggered_rules contains "DG-001" if {
    input.event_type == "external_sharing"
    some _DG_001_item in input.data_items
    every t in ["PII"] { t in _DG_001_item.regulatory_tags }
    some _DG_001_dest in input.data_destinations
    every c in ["external"] { c in _DG_001_dest.data_destination_categories }
    _DG_001_dest.data_destination_trust_level == "UNTRUSTED_EXTERNAL"
}

triggered_rules contains "DG-002" if {
    input.event_type == "external_sharing"
    some _DG_002_item in input.data_items
    every t in ["PHI"] { t in _DG_002_item.regulatory_tags }
    some _DG_002_dest in input.data_destinations
    every c in ["external"] { c in _DG_002_dest.data_destination_categories }
    _DG_002_dest.data_destination_trust_level == "UNTRUSTED_EXTERNAL"
}

triggered_rules contains "DG-004" if {
    input.event_type == "external_sharing"
    some _DG_004_item in input.data_items
    _DG_004_item.classification_level == "RESTRICTED"
    some _DG_004_dest in input.data_destinations
    every c in ["external"] { c in _DG_004_dest.data_destination_categories }
    _DG_004_dest.data_destination_trust_level == "UNTRUSTED_EXTERNAL"
}

_rule_decisions := {"DG-001": {"risk_level": "critical", "enforcement_type": "block", "allowed_actions": ["redact"], "explanation": "PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk.", "confidence": 0.95}, "DG-002": {"risk_level": "critical", "enforcement_type": "block", "allowed_actions": [], "explanation": "PHI (Protected Health Information) detected in payload sent to UNTRUSTED_EXTERNAL destination. HIPAA violation.", "confidence": 0.95}, "DG-004": {"risk_level": "critical", "enforcement_type": "block", "allowed_actions": [], "explanation": "RESTRICTED document detected in external sharing event to UNTRUSTED_EXTERNAL destination. Sharing prohibited.", "confidence": 0.95}}
_rule_order := {"DG-001": 0, "DG-002": 1, "DG-004": 2}
_risk_level_rank := {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4, "unknown": 5}
_enforcement_rank := {"block": 0, "quarantine": 1, "require_approval": 2, "redact": 3, "mask": 4, "anonymize": 5, "encrypt": 6, "escalate": 7, "notify": 8, "warn": 9, "log_only": 10, "audit": 11, "allow": 12}

default policy_decision := {"rule_id": "0000", "rule_name": "fallback rule", "risk_level": "none", "enforcement_type": "allow", "allowed_actions": [], "explanation": "No rules fired, falling back to default rule", "confidence": 1.0, "triggered_rules": []}

policy_decision := decision if {
    count(triggered_rules) > 0
    ranked := [[
        _risk_level_rank[_rule_decisions[id].risk_level],
        _enforcement_rank[_rule_decisions[id].enforcement_type],
        id,
    ] |
        some id in triggered_rules
    ]
    winner_id := sort(ranked)[0][2]
    decision := object.union(_rule_decisions[winner_id], {
        "triggered_rules": sort([id | some id in triggered_rules]),
        "rule_combining_mode": "most_restrictive",
    })
}
