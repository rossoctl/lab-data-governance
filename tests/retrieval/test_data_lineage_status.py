"""The trace-level lineage status on the read path (issue #120, ADR-0027 D6).

``get_data_lineage`` gains the trace's ``complete``/``partial`` coverage alongside
the per-leg metadata. The ``{"legs": [...]}`` envelope #118 shaped exists for
exactly this: the status is a whole-trace fact and has nowhere to live on a leg.

Still a **pure lookup** (ADR-0027 D7): the status is read from
``lineage_trace_status`` (migration 0012), never recomputed here from the presence
of a payload gap. Two copies of the cutoff rule — one in the traversal, one in the
read's SQL — is the drift this table's existence avoids.

The absence cases matter as much as the present ones and get equal coverage below:
a trace whose status row has not landed reads as *unknown*, never ``complete``
(ADR-0027 D6 "Reading the status").
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance import retrieval

_TID = "trace-status-1"
_OTHER_TID = "trace-status-2"
_ENT_ID = "ent-1"
_IX_ID = "ix-1"


def _seed_trace(conn: psycopg.Connection, trace_id: str, ix_id: str) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, "
        "callee_entity_id, summary) VALUES (%s, %s, %s, %s, 'did a thing')",
        (ix_id, trace_id, _ENT_ID, _ENT_ID),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error, original_seq) "
        "VALUES (%s, 'request', '2026-01-01T00:00:00Z', 'reqhash', false, 1)",
        (ix_id,),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error, original_seq) "
        "VALUES (%s, 'response', '2026-01-01T00:00:02Z', NULL, false, 2)",
        (ix_id,),
    )


def _seed_status(
    conn: psycopg.Connection, trace_id: str, status: str, stopped_at_seq: int | None
) -> None:
    conn.execute(
        "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
        "VALUES (%s, %s, %s)",
        (trace_id, status, stopped_at_seq),
    )


@pytest.fixture()
def seeded(configured_db: str) -> str:
    """One trace whose response leg carries no payload — the D6 gap — with the
    processor's ``partial`` verdict recorded."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        _seed_trace(conn, _TID, _IX_ID)
        (gap_leg_seq,) = conn.execute(
            "SELECT seq FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (_IX_ID,),
        ).fetchone()
        _seed_status(conn, _TID, "partial", gap_leg_seq)
        conn.commit()
    return _TID


def _stop_seq(configured_db: str, ix_id: str = _IX_ID) -> int:
    with psycopg.connect(configured_db) as conn:
        (seq,) = conn.execute(
            "SELECT seq FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (ix_id,),
        ).fetchone()
    return seq


def test_partial_status_is_served_with_the_stop_position(
    seeded: str, configured_db: str
) -> None:
    """The status rides on the trace-level result, beside the legs."""
    result = retrieval.get_data_lineage(seeded)
    assert result.status == "partial"
    assert result.stopped_at_seq == _stop_seq(configured_db)


def test_complete_status_has_no_stop_position(configured_db: str) -> None:
    """A fully-covered trace reads ``complete``, with nothing to point at."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        _seed_trace(conn, _TID, _IX_ID)
        _seed_status(conn, _TID, "complete", None)
        conn.commit()

    result = retrieval.get_data_lineage(_TID)
    assert result.status == "complete"
    assert result.stopped_at_seq is None


def test_status_is_null_when_not_yet_derived(configured_db: str) -> None:
    """No status row → ``status=None``, meaning *unknown*, never ``complete``
    (ADR-0027 D6). This is the eventual-consistency window, so it is the common
    case rather than an edge one — the legs assertion below keeps it honest that
    the read still serves them."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, original_seq) "
            "VALUES (%s, 'agent', 'nk-1', 'Agent One', 'span-attr', 1)",
            (_ENT_ID,),
        )
        _seed_trace(conn, _TID, _IX_ID)
        conn.commit()

    result = retrieval.get_data_lineage(_TID)
    assert result.legs, "sanity: the legs are there, only the status is missing"
    assert result.status is None
    assert result.stopped_at_seq is None


def test_status_is_scoped_to_the_requested_trace(seeded: str, configured_db: str) -> None:
    """``lineage_trace_status`` is keyed by trace; a neighbour's ``partial`` must
    not bleed into this trace's answer (or the reverse)."""
    with psycopg.connect(configured_db) as conn:
        _seed_trace(conn, _OTHER_TID, "ix-2")
        _seed_status(conn, _OTHER_TID, "complete", None)
        conn.commit()

    assert retrieval.get_data_lineage(seeded).status == "partial"
    assert retrieval.get_data_lineage(_OTHER_TID).status == "complete"


def test_unknown_trace_has_no_status(seeded: str) -> None:
    """An unknown trace is an empty typed result all round — no legs, no claim."""
    result = retrieval.get_data_lineage("no-such-trace")
    assert result.legs == []
    assert result.status is None
    assert result.stopped_at_seq is None


def test_status_survives_a_missing_status_table(seeded: str, configured_db: str) -> None:
    """A deployment mid-upgrade (0011 applied, 0012 not) still serves the per-leg
    lineage, with the status simply unknown — an unmigrated status table must not
    take the whole read down, and must not fabricate ``complete``."""
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE IF EXISTS lineage_trace_status CASCADE")
        conn.commit()

    result = retrieval.get_data_lineage(seeded)
    assert result.status is None
    assert result.stopped_at_seq is None
    assert {(r.interaction_id, r.leg_type) for r in result.legs} == {
        (_IX_ID, "request"),
        (_IX_ID, "response"),
    }


def test_the_read_does_not_recompute_the_status_from_the_gap(
    seeded: str, configured_db: str
) -> None:
    """A pure lookup (ADR-0027 D7), and the reason the status got its own table:
    the read reports what the PROCESSOR concluded, never its own reading of the
    legs. Here the recorded status is deliberately ``complete`` while a leg's
    payload is absent — a state the running system would not produce, which is
    exactly what makes it a probe. A read that re-derived the cutoff from
    ``payload_hash IS NULL`` would answer ``partial`` and would be a second,
    drifting copy of D6's rule."""
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE lineage_trace_status SET status = 'complete', "
            "stopped_at_seq = NULL WHERE trace_id = %s",
            (_TID,),
        )
        conn.commit()

    result = retrieval.get_data_lineage(seeded)
    assert result.status == "complete"
    assert result.stopped_at_seq is None
