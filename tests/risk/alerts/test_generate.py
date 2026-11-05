"""Alert generation & supersession (issue #104) against a real migrated DB.

Acceptance criteria covered, by test name:

- AC-DAS-002 — a risky (negative-scenario) trace record generates an alert;
  a clean (positive) one does not.
- AC-DAS-013 — recomputation that does not raise the level: no new alert,
  ``duplicate_count`` increments.
- AC-DAS-014 — recomputation that raises the level: new alert, prior's
  ``superseded_by`` set, prior's ``status`` unchanged.
- AC-DAS-024 — floor ``low`` alerts on a low record; floor ``medium`` does not.
- AC-DAS-026 — one rule, two entities: title/description match the
  construction rule exactly.
- AC-DAS-035 — for each floor in critical/high/medium/low, generation over
  the demo set produces exactly the expected alert set.

Also: a closed prior alert (``resolved``/``false_positive``) is no longer the
trace's open alert, so a recurrence opens a fresh, unlinked one.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest

from data_governance import db
from data_governance.risk import config
from data_governance.risk.alerts.generate import generate_for_trace_risk_record, validate_config
from data_governance.risk.rules import catalog

_VARIED_CATALOG = Path(__file__).resolve().parents[1] / "rules" / "fixtures" / "catalog_varied.json"


# --- fixtures / helpers --------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_alert_config(monkeypatch):
    monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", "low")


def _seed_entities(dsn: str, entities: list[tuple[str, str, str]]) -> None:
    with psycopg.connect(dsn) as conn:
        for entity_id, kind, display_name in entities:
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, "
                "detected_from, original_seq) VALUES (%s, %s, %s, %s, 'test', 0) "
                "ON CONFLICT (id) DO NOTHING",
                (entity_id, kind, entity_id, display_name),
            )
        conn.commit()


def _seed_interaction(dsn: str, trace_id: str, caller: str, callee: str) -> str:
    interaction_id = f"ix-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 's')",
            (interaction_id, trace_id, caller, callee),
        )
        conn.commit()
    return interaction_id


def _interaction_risk(
    dsn: str, trace_id: str, interaction_id: str, caller: str, callee: str, rule_ids: list[str]
) -> str:
    """An interaction risk record for *interaction_id*; *caller*/*callee* are
    the record's own snapshot, which can differ from the interaction (#279)."""
    with psycopg.connect(dsn) as conn:
        (record_id,) = conn.execute(
            "INSERT INTO interaction_risk_records (interaction_id, trace_id, "
            "caller_entity_id, callee_entity_id, version, computed_at, risk_level, "
            "policy_event_count, triggered_rule_ids) "
            "VALUES (%s, %s, %s, %s, 1, now(), %s, %s, %s) RETURNING interaction_risk_id",
            (interaction_id, trace_id, caller, callee,
             "critical" if rule_ids else "none", len(rule_ids), rule_ids),
        ).fetchone()
        conn.commit()
    return str(record_id)


def _trace_risk(
    dsn: str,
    trace_id: str,
    level: str,
    *,
    version: int = 1,
    enforcement: str | None = None,
    rule_ids: list[str] | None = None,
    contributing: list[str] | None = None,
) -> str:
    with psycopg.connect(dsn) as conn:
        (record_id,) = conn.execute(
            "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
            "trace_risk_level, trace_enforcement_type, interaction_count, "
            "policy_event_count, triggered_rule_ids, contributing_interaction_risk_ids) "
            "VALUES (%s, %s, now(), %s, %s, 1, 1, %s, %s::uuid[]) RETURNING trace_risk_id",
            (trace_id, version, level, enforcement, rule_ids or [], contributing or []),
        ).fetchone()
        conn.commit()
    return str(record_id)


def _generate(record_id: str):
    with db.transaction() as tx:
        return generate_for_trace_risk_record(tx, record_id)


def _alerts(dsn: str, trace_id: str) -> list[dict]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT alert_id, trace_risk_record_id, risk_level, enforcement_type, "
            "title, description, involved_entities, triggered_rule_ids, status, "
            "alert_type, dedup_key, duplicate_count, superseded_by "
            "FROM alerts WHERE trace_id = %s ORDER BY \"timestamp\", alert_id",
            (trace_id,),
        ).fetchall()
    keys = (
        "alert_id", "trace_risk_record_id", "risk_level", "enforcement_type",
        "title", "description", "involved_entities", "triggered_rule_ids",
        "status", "alert_type", "dedup_key", "duplicate_count", "superseded_by",
    )
    return [
        dict(zip(keys, (str(r[0]), str(r[1])) + tuple(r[2:-1])
                 + (str(r[-1]) if r[-1] is not None else None,)))
        for r in rows
    ]


def _set_alert(dsn: str, alert_id: str, **cols) -> None:
    with psycopg.connect(dsn) as conn:
        for col, value in cols.items():
            conn.execute(f'UPDATE alerts SET "{col}" = %s WHERE alert_id = %s', (value, alert_id))
        conn.commit()


# --- AC-DAS-002 / AC-DAS-026: first alert, shape ---------------------------------


def test_first_risky_record_creates_an_open_standard_alert(configured_db: str):
    trace = "t-first"
    _seed_entities(configured_db, [("e-clin", "agent", "priorauth-clinical"),
                                   ("e-partner", "service", "api.exfil-partner.example"),
                                   ("e-weather", "tool", "get-weather")])
    ix_partner = _seed_interaction(configured_db, trace, "e-clin", "e-partner")
    ix_weather = _seed_interaction(configured_db, trace, "e-clin", "e-weather")
    risky = _interaction_risk(configured_db, trace, ix_partner, "e-clin", "e-partner", ["DG-001"])
    clean = _interaction_risk(configured_db, trace, ix_weather, "e-clin", "e-weather", [])
    record = _trace_risk(configured_db, trace, "critical", enforcement="block",
                         rule_ids=["DG-001"], contributing=[risky, clean])

    outcome = _generate(record)

    assert outcome.action == "created"
    (alert,) = _alerts(configured_db, trace)
    assert alert["alert_id"] == outcome.alert_id
    assert alert["trace_risk_record_id"] == record
    assert alert["risk_level"] == "critical" and alert["enforcement_type"] == "block"
    assert alert["status"] == "new" and alert["alert_type"] == "standard"
    assert alert["duplicate_count"] == 0 and alert["superseded_by"] is None
    assert alert["triggered_rule_ids"] == ["DG-001"]
    assert alert["dedup_key"] is None, "#105's dedup key is out of MVP scope"
    # AC-DAS-026: one rule, two entities — exact construction. The title
    # names only the entities of the interaction that fired the rule.
    assert alert["title"] == "pii_to_untrusted_external — api.exfil-partner.example, priorauth-clinical"
    assert alert["description"] == (
        "PII detected in payload sent to UNTRUSTED_EXTERNAL destination. "
        "Data exfiltration risk."
    )
    assert alert["involved_entities"] == [
        {"entity_id": "e-partner", "entity_kind": "service",
         "display_name": "api.exfil-partner.example"},
        {"entity_id": "e-weather", "entity_kind": "tool", "display_name": "get-weather"},
        {"entity_id": "e-clin", "entity_kind": "agent", "display_name": "priorauth-clinical"},
    ], "involved_entities keeps every entity of the trace"


def test_title_names_the_interaction_s_current_entities_not_the_record_s(configured_db: str):
    """A record keeps the callee it was computed with after the interaction is
    re-keyed (#279). The title reads the current interaction, as
    involved_entities does, so it never names an entity its alert omits."""
    trace = "t-rekey"
    _seed_entities(configured_db, [("e-agent", "agent", "caller-agent"),
                                   ("e-old", "service", "old-callee"),
                                   ("e-new", "service", "new-callee")])
    ix = _seed_interaction(configured_db, trace, "e-agent", "e-new")
    stale = _interaction_risk(configured_db, trace, ix, "e-agent", "e-old", ["DG-001"])
    record = _trace_risk(configured_db, trace, "critical", rule_ids=["DG-001"], contributing=[stale])

    _generate(record)

    (alert,) = _alerts(configured_db, trace)
    assert alert["title"] == "pii_to_untrusted_external — caller-agent, new-callee"
    assert {e["display_name"] for e in alert["involved_entities"]} == {"caller-agent", "new-callee"}


def test_title_leads_with_the_most_severe_rule_not_the_smallest_id(configured_db: str, monkeypatch):
    """FX-LOW-ALLOW (low) sorts before FX-MED-ESC (medium) by id; the title
    and the description order by catalog severity."""
    monkeypatch.setattr(catalog, "_RULES_SOURCE", _VARIED_CATALOG)
    catalog.reload()
    try:
        record = _trace_risk(configured_db, "t-rank", "medium",
                             rule_ids=["FX-LOW-ALLOW", "FX-MED-ESC"])
        _generate(record)
        (alert,) = _alerts(configured_db, "t-rank")
        rules = {r["rule_id"]: r for r in catalog.list_rules()}
    finally:
        catalog.reload()
    assert alert["title"] == "fixture_medium_escalate"
    assert alert["description"] == "; ".join(
        rules[r]["explanation"] for r in ("FX-MED-ESC", "FX-LOW-ALLOW"))


def test_clean_record_generates_no_alert(configured_db: str):
    """AC-DAS-002 positive arm: trace risk none never alerts."""
    record = _trace_risk(configured_db, "t-clean", "none", enforcement="allow")
    outcome = _generate(record)
    assert outcome.action == "skipped" and outcome.reason == "below_floor"
    assert _alerts(configured_db, "t-clean") == []


def test_multiple_rules_title_leads_with_most_severe_and_description_concatenates(
    configured_db: str,
):
    record = _trace_risk(configured_db, "t-multi", "critical",
                         rule_ids=["DG-004", "DG-002", "DG-001"])
    _generate(record)
    (alert,) = _alerts(configured_db, "t-multi")
    # All three are critical in the catalog; ties break on rule id -> DG-001.
    assert alert["title"] == "pii_to_untrusted_external"
    assert alert["description"].split("; ")[0].startswith("PII detected")
    assert alert["description"].split("; ")[1].startswith("PHI (Protected")
    assert alert["description"].split("; ")[2].startswith("RESTRICTED document")
    assert alert["triggered_rule_ids"] == ["DG-001", "DG-002", "DG-004"]


def test_unknown_rule_id_is_named_by_id_and_gets_a_fallback_description(configured_db: str):
    record = _trace_risk(configured_db, "t-unknown", "high", rule_ids=["ZZ-9"])
    _generate(record)
    (alert,) = _alerts(configured_db, "t-unknown")
    assert alert["title"] == "ZZ-9"
    assert alert["description"] == "Trace risk level high for trace t-unknown"


def test_record_with_no_rules_titles_by_level(configured_db: str):
    record = _trace_risk(configured_db, "t-norules", "medium")
    _generate(record)
    (alert,) = _alerts(configured_db, "t-norules")
    assert alert["title"] == "Trace risk medium"


# --- AC-DAS-013 / AC-DAS-014: supersession -------------------------------------


def test_same_level_recompute_counts_a_duplicate(configured_db: str):
    trace = "t-same"
    first = _trace_risk(configured_db, trace, "high", rule_ids=["DG-001"])
    second = _trace_risk(configured_db, trace, "high", version=2, rule_ids=["DG-001"])

    assert _generate(first).action == "created"
    outcome = _generate(second)

    assert outcome.action == "duplicate"
    (alert,) = _alerts(configured_db, trace)
    assert alert["duplicate_count"] == 1 and alert["status"] == "new"
    assert alert["trace_risk_record_id"] == first, "the open alert still references v1"


def test_lower_level_recompute_counts_a_duplicate_and_keeps_the_alert_open(
    configured_db: str,
):
    trace = "t-lower"
    first = _trace_risk(configured_db, trace, "critical", rule_ids=["DG-001"])
    second = _trace_risk(configured_db, trace, "low", version=2)
    _generate(first)
    assert _generate(second).action == "duplicate"
    (alert,) = _alerts(configured_db, trace)
    assert alert["risk_level"] == "critical" and alert["duplicate_count"] == 1


def test_raised_level_supersedes_without_touching_the_prior_status(configured_db: str):
    trace = "t-raise"
    first = _trace_risk(configured_db, trace, "low")
    _generate(first)
    (prior,) = _alerts(configured_db, trace)
    _set_alert(configured_db, prior["alert_id"], status="acknowledged", assigned_to="ops")
    second = _trace_risk(configured_db, trace, "critical", version=2,
                         enforcement="block", rule_ids=["DG-001", "DG-002"])

    outcome = _generate(second)

    assert outcome.action == "superseded"
    assert outcome.superseded_alert_id == prior["alert_id"]
    old, new = _alerts(configured_db, trace)
    assert old["alert_id"] == prior["alert_id"]
    assert old["superseded_by"] == new["alert_id"]
    assert old["status"] == "acknowledged", "prior status is never changed"
    assert old["duplicate_count"] == 0
    assert new["alert_id"] == outcome.alert_id
    assert new["trace_risk_record_id"] == second
    assert new["risk_level"] == "critical" and new["status"] == "new"
    assert new["superseded_by"] is None


def test_supersession_chain_follows_the_newest_open_alert(configured_db: str):
    """low -> high -> critical: each raise supersedes the CURRENT open
    alert, so the chain is old->mid->new with exactly one open head."""
    trace = "t-chain"
    for version, level in enumerate(("low", "high", "critical"), start=1):
        _generate(_trace_risk(configured_db, trace, level, version=version))
    a, b, c = _alerts(configured_db, trace)
    assert a["superseded_by"] == b["alert_id"]
    assert b["superseded_by"] == c["alert_id"]
    assert c["superseded_by"] is None

    # A later same-level recompute counts on the head, not the ancestors.
    _generate(_trace_risk(configured_db, trace, "critical", version=4))
    a, b, c = _alerts(configured_db, trace)
    assert (a["duplicate_count"], b["duplicate_count"], c["duplicate_count"]) == (0, 0, 1)


def test_redelivered_record_is_a_noop(configured_db: str):
    """Idempotency per record: the same trace risk record delivered twice
    (crash before the cursor advance committed) neither re-creates nor
    re-counts."""
    trace = "t-redeliver"
    record = _trace_risk(configured_db, trace, "high")
    assert _generate(record).action == "created"
    outcome = _generate(record)
    assert outcome.action == "skipped" and outcome.reason == "already_alerted"
    (alert,) = _alerts(configured_db, trace)
    assert alert["duplicate_count"] == 0


def test_missing_record_is_skipped(configured_db: str):
    outcome = _generate(str(uuid.uuid4()))
    assert outcome.action == "skipped" and outcome.reason == "trace_risk_record_missing"


# --- closed prior alerts ------------------------------------------------------


@pytest.mark.parametrize("closed", ["resolved", "false_positive"])
def test_closed_prior_reopens_a_fresh_unlinked_alert(configured_db: str, closed: str):
    """A closed alert is not the trace's open alert: the recurrence opens a
    new one, and the closed one is not superseded (it was closed, not
    overtaken)."""
    trace = f"t-closed-{closed}"
    _generate(_trace_risk(configured_db, trace, "high", rule_ids=["DG-001"]))
    (prior,) = _alerts(configured_db, trace)
    _set_alert(configured_db, prior["alert_id"], status=closed)

    outcome = _generate(_trace_risk(configured_db, trace, "high", version=2,
                                    rule_ids=["DG-001"]))

    assert outcome.action == "created"
    old, new = _alerts(configured_db, trace)
    assert old["status"] == closed
    assert old["superseded_by"] is None and old["duplicate_count"] == 0
    assert new["status"] == "new"

    # The fresh alert is now the open one: the next recompute counts on it.
    _generate(_trace_risk(configured_db, trace, "high", version=3, rule_ids=["DG-001"]))
    old, new = _alerts(configured_db, trace)
    assert (old["duplicate_count"], new["duplicate_count"]) == (0, 1)


# --- AC-DAS-024 / AC-DAS-035: the floor ------------------------------------------


def test_floor_low_alerts_on_low_and_floor_medium_does_not(configured_db: str, monkeypatch):
    low_a = _trace_risk(configured_db, "t-floor-a", "low", enforcement="allow")
    assert _generate(low_a).action == "created"

    monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", "medium")
    low_b = _trace_risk(configured_db, "t-floor-b", "low", enforcement="allow")
    assert _generate(low_b).action == "skipped"
    assert _alerts(configured_db, "t-floor-b") == []


_DEMO_SET = {"none": "t-d-none", "low": "t-d-low", "medium": "t-d-medium",
             "high": "t-d-high", "critical": "t-d-critical"}


@pytest.mark.parametrize(
    "floor, expected_levels",
    [
        ("critical", {"critical"}),
        ("high", {"high", "critical"}),
        ("medium", {"medium", "high", "critical"}),
        ("low", {"low", "medium", "high", "critical"}),
    ],
)
def test_floor_sweep_produces_exactly_the_expected_alert_set(
    configured_db: str, monkeypatch, floor, expected_levels
):
    """AC-DAS-035 over a demo set holding one trace per level."""
    monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", floor)
    alerted = set()
    for level, trace in _DEMO_SET.items():
        if _generate(_trace_risk(configured_db, trace, level)).action == "created":
            alerted.add(level)
    assert alerted == expected_levels
    with psycopg.connect(configured_db) as conn:
        (count,) = conn.execute("SELECT count(*) FROM alerts").fetchone()
    assert count == len(expected_levels)


# --- config guard ---------------------------------------------------------------


@pytest.mark.parametrize("floor", ["High", "none", ""])
def test_validate_config_refuses_a_floor_it_would_misread(monkeypatch, floor):
    monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", floor)
    with pytest.raises(ValueError, match="RISK_ALERT_MIN_RISK_LEVEL"):
        validate_config()


def test_validate_config_accepts_every_alertable_floor(monkeypatch):
    for floor in ("low", "medium", "high", "critical"):
        monkeypatch.setattr(config, "ALERT_MIN_RISK_LEVEL", floor)
        validate_config()
