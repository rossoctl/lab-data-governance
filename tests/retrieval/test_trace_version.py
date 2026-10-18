"""``get_trace_version`` — the Trace version token (ADR-0034).

The token is compared for equality only, so the tests assert exactly that:
it is stable while nothing changes, and it changes on each kind of derived
write a live page cares about — a leg, an entity attribution, a lineage row,
a lineage status flip, a risk record, each write to an alert — and never
because of another trace.
"""

from __future__ import annotations

import psycopg

from data_governance import retrieval

_TID = "ver-trace"
_ZERO = "legs=0;ix=0;es=0;lin=0;st=@0;irr=0;trr=0;al=0"


def _seed_entity(conn, *, entity_id: str, trace_id: str, span_id: str) -> None:
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, "
        "started_at, attributes, seq, arrival_seq, observed_at) "
        "VALUES (%s, %s, NULL, 'INTERNAL', 'call', now(), '{}'::jsonb, "
        "nextval('spans_seq'), currval('spans_seq'), now())",
        (trace_id, span_id),
    )
    conn.execute(
        "INSERT INTO entities (id, kind, natural_key, display_name, namespace, "
        "detected_from, original_seq) "
        "VALUES (%s, 'agent', %s, %s, 'team1', 'span-attr', 1) "
        "ON CONFLICT (id) DO NOTHING",
        (entity_id, f"nk-{entity_id}", entity_id),
    )
    conn.execute(
        "INSERT INTO entity_spans (entity_id, trace_id, span_id, role) "
        "VALUES (%s, %s, %s, 'identified_via')",
        (entity_id, trace_id, span_id),
    )
    conn.commit()


def _add_interaction(conn, *, ix_id: str, trace_id: str, entity_id: str) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'did a thing')",
        (ix_id, trace_id, entity_id, entity_id),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES (%s, 'request', now(), NULL, false)",
        (ix_id,),
    )
    conn.commit()


def _add_interaction_risk(conn, *, ix_id: str, trace_id: str, version: int) -> None:
    conn.execute(
        "INSERT INTO interaction_risk_records (interaction_id, trace_id, "
        "caller_entity_id, callee_entity_id, version, computed_at, risk_level, "
        "policy_event_count) VALUES (%s, %s, 'e1', 'e1', %s, now(), 'low', 0)",
        (ix_id, trace_id, version),
    )
    conn.commit()


def _add_trace_risk(conn, *, trace_id: str, version: int) -> str:
    row = conn.execute(
        "INSERT INTO trace_risk_records (trace_id, version, computed_at, "
        "trace_risk_level, interaction_count, policy_event_count) "
        "VALUES (%s, %s, now(), 'high', 1, 1) RETURNING trace_risk_id",
        (trace_id, version),
    ).fetchone()
    conn.commit()
    return str(row[0])


def _add_alert(conn, *, trace_id: str, trace_risk_id: str) -> str:
    row = conn.execute(
        "INSERT INTO alerts (\"timestamp\", trace_id, trace_risk_record_id, "
        "risk_level, title, description, status, alert_type) "
        "VALUES (now(), %s, %s, 'high', 'a rule fired', 'why', 'new', 'standard') "
        "RETURNING alert_id",
        (trace_id, trace_risk_id),
    ).fetchone()
    conn.commit()
    return str(row[0])


def test_fresh_trace_is_all_zero_and_stable(configured_db: str) -> None:
    assert retrieval.get_trace_version(_TID) == _ZERO
    assert retrieval.get_trace_version(_TID) == retrieval.get_trace_version(_TID)


def test_each_derived_write_changes_the_token(configured_db: str, raw_conn) -> None:
    seen = [retrieval.get_trace_version(_TID)]

    def step() -> None:
        token = retrieval.get_trace_version(_TID)
        assert token not in seen, f"token did not change: {token}"
        seen.append(token)

    _seed_entity(raw_conn, entity_id="e1", trace_id=_TID, span_id="s1")
    step()  # an entity attributed before any interaction exists
    _add_interaction(raw_conn, ix_id="ix1", trace_id=_TID, entity_id="e1")
    step()  # a leg
    raw_conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES ('ix1', 'response', now(), NULL, false)"
    )
    raw_conn.commit()
    step()  # the other leg of the same interaction
    raw_conn.execute(
        "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, seq) "
        "VALUES ('ix1', 'request', '{}', 1)"
    )
    raw_conn.commit()
    step()  # a lineage row, which fires no notification of its own
    raw_conn.execute(
        "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
        "VALUES (%s, 'partial', 1)",
        (_TID,),
    )
    raw_conn.commit()
    step()  # the coverage status
    raw_conn.execute(
        "UPDATE lineage_trace_status SET status = 'complete', stopped_at_seq = NULL "
        "WHERE trace_id = %s",
        (_TID,),
    )
    raw_conn.commit()
    step()  # …and its flip to complete, with no new row anywhere


def test_each_risk_write_changes_the_token(configured_db: str, raw_conn) -> None:
    """The risk processors write after derivation, so a trace's decision or
    alert is often its last write — each must move the token on its own,
    including the alert updates that add no row."""
    _seed_entity(raw_conn, entity_id="e1", trace_id=_TID, span_id="s1")
    _add_interaction(raw_conn, ix_id="ix1", trace_id=_TID, entity_id="e1")
    seen = [retrieval.get_trace_version(_TID)]

    def step() -> None:
        token = retrieval.get_trace_version(_TID)
        assert token not in seen, f"token did not change: {token}"
        seen.append(token)

    _add_interaction_risk(raw_conn, ix_id="ix1", trace_id=_TID, version=1)
    step()  # an interaction risk record
    _add_interaction_risk(raw_conn, ix_id="ix1", trace_id=_TID, version=2)
    step()  # …and its recompute, a new version of the same interaction
    first = _add_trace_risk(raw_conn, trace_id=_TID, version=1)
    step()  # the trace risk record
    alert = _add_alert(raw_conn, trace_id=_TID, trace_risk_id=first)
    step()  # its alert
    _add_trace_risk(raw_conn, trace_id=_TID, version=2)
    step()  # a trace risk recompute
    raw_conn.execute(
        "UPDATE alerts SET duplicate_count = duplicate_count + 1 WHERE alert_id = %s",
        (alert,),
    )
    raw_conn.commit()
    step()  # the recompute counted as a duplicate of the open alert: no new row
    third = _add_trace_risk(raw_conn, trace_id=_TID, version=3)
    step()
    newer = _add_alert(raw_conn, trace_id=_TID, trace_risk_id=third)
    step()  # a superseding alert…
    raw_conn.execute(
        "UPDATE alerts SET superseded_by = %s WHERE alert_id = %s", (newer, alert)
    )
    raw_conn.commit()
    step()  # …and the supersession of the old one, an update
    raw_conn.execute(
        "UPDATE alerts SET status = 'acknowledged' WHERE alert_id = %s", (newer,)
    )
    raw_conn.commit()
    step()  # an operator's status change


def test_sidecar_style_rederivation_changes_the_token(configured_db: str, raw_conn) -> None:
    """The sidecar derivation deletes and re-inserts a trace's rows per span
    (ADR-0030): same row count, same ids, new seqs — must still read as changed."""
    _seed_entity(raw_conn, entity_id="e1", trace_id=_TID, span_id="s1")
    _add_interaction(raw_conn, ix_id="ix1", trace_id=_TID, entity_id="e1")
    before = retrieval.get_trace_version(_TID)
    raw_conn.execute("DELETE FROM interaction_legs WHERE interaction_id = 'ix1'")
    raw_conn.execute("DELETE FROM interactions WHERE id = 'ix1'")
    raw_conn.commit()
    _add_interaction(raw_conn, ix_id="ix1", trace_id=_TID, entity_id="e1")
    assert retrieval.get_trace_version(_TID) != before


def test_another_traces_writes_do_not_change_the_token(configured_db: str, raw_conn) -> None:
    _seed_entity(raw_conn, entity_id="e1", trace_id=_TID, span_id="s1")
    before = retrieval.get_trace_version(_TID)
    _seed_entity(raw_conn, entity_id="e2", trace_id="elsewhere", span_id="s2")
    _add_interaction(raw_conn, ix_id="ix-else", trace_id="elsewhere", entity_id="e2")
    _add_interaction_risk(raw_conn, ix_id="ix-else", trace_id="elsewhere", version=1)
    other = _add_trace_risk(raw_conn, trace_id="elsewhere", version=1)
    _add_alert(raw_conn, trace_id="elsewhere", trace_risk_id=other)
    assert retrieval.get_trace_version(_TID) == before


def test_before_any_migration_the_token_is_all_zero(pg_dsn: str) -> None:
    """An unmigrated DB is never an error, like every other retrieval read."""
    from data_governance import db

    db.close_pool()
    db.configure(pg_dsn)
    try:
        assert retrieval.get_trace_version(_TID) == _ZERO
    finally:
        db.close_pool()
