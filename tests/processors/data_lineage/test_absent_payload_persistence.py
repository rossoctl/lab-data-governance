"""Persisting the prefix cutoff + the trace status (issue #120, ADR-0027 D6).

The pure cutoff rule is tested in ``test_absent_payload_cutoff.py``; this file is
about what reaches the database, where two things get hard:

1. **Stale rows.** ``driver.process_leg`` re-derives a whole trace per arriving
   leg, so when a re-derivation produces a SHORTER prefix than an earlier one did —
   a leg's payload is rewritten to NULL, or a gap appears mid-trace as legs are
   re-derived in place by P-interactions — the rows past the new cutoff linger from
   the longer answer, and an upsert is silent about them. The API would then serve
   lineage for legs *after* the gap while the status says partial: worse than silent
   truncation, because it is self-contradictory. The driver therefore also deletes
   the trace's rows **that the current derivation did not produce** — set
   membership, NOT ``seq >= stop`` (ADR-0027 D9: ``seq`` is re-allocated on rewrite,
   so a threshold would spare exactly the stale rows).
2. **partial → complete.** The reverse transition, an explicit acceptance
   criterion: a late response payload arrives and the trace becomes whole. The
   status row must flip and the stop position must clear, with no stale
   ``partial`` left to shadow it.

Seeding writes straight into the derived tables (same helpers as
``test_driver.py``) because the subject is the lineage driver, not the
interactions pipeline.
"""

from __future__ import annotations

import psycopg

from data_governance import db
from data_governance.processors.data_lineage import driver

TRACE = "t" * 32
OTHER_TRACE = "o" * 32


# --- seeding helpers (mirroring tests/processors/data_lineage/test_driver.py) --


def _entity(conn: psycopg.Connection, *, eid: str, kind: str, natural_key: str) -> None:
    conn.execute(
        "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, "
        "seq, original_seq) VALUES (%s, %s, %s, %s, 'observed', 1, 1) "
        "ON CONFLICT (natural_key) DO NOTHING",
        (eid, kind, natural_key, natural_key),
    )


def _payload(conn: psycopg.Connection, content_hash: str) -> None:
    conn.execute(
        "INSERT INTO interaction_payloads (content_hash, content_kind, content, byte_size) "
        "VALUES (%s, 'unknown', %s::jsonb, 2) ON CONFLICT (content_hash) DO NOTHING",
        (content_hash, "{}"),
    )


def _interaction(
    conn: psycopg.Connection, *, ix_id: str, trace_id: str, caller: str, callee: str
) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, "
        "summary) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
        (ix_id, trace_id, caller, callee, f"{caller} -> {callee}"),
    )


def _leg(
    conn: psycopg.Connection,
    *,
    ix_id: str,
    leg_type: str,
    payload_hash: str | None,
    original_seq: int,
) -> None:
    """Write (or rewrite) one leg. A ``None`` hash is an ABSENT payload — the D6
    gap. Rewriting re-allocates ``seq`` from the sequence, exactly as
    P-interactions' in-place re-derivation does (migration 0010's trigger covers
    UPDATE for this reason)."""
    if payload_hash is not None:
        _payload(conn, payload_hash)
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error, original_seq) VALUES (%s, %s, now(), %s, false, %s) "
        "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
        "payload_hash = EXCLUDED.payload_hash, seq = nextval('interaction_legs_seq')",
        (ix_id, leg_type, payload_hash, original_seq),
    )


def _seed_entities(conn: psycopg.Connection) -> None:
    _entity(conn, eid="e_user", kind="user", natural_key="user:alice")
    _entity(conn, eid="e_agent", kind="agent", natural_key="agent:(demo,advisor)")
    _entity(conn, eid="e_llm", kind="llm", natural_key="llm:host/gpt")


# The spec's worked-example shape (``docs/data_lineage_alg.md:140-153``) as legs in
# execution order: user→agent, agent→llm twice, then the agent's answer back.
_WORKED_EXAMPLE = [
    ("ix_ua", "request", "p1"),
    ("ix_al1", "request", "p2"),
    ("ix_al1", "response", "p3"),  # the leg the gap tests unpayload
    ("ix_al2", "request", "p4"),
    ("ix_al2", "response", "p5"),
    ("ix_ua", "response", "p6"),
]


def _write_legs(dsn: str, *, unpayloaded: tuple[str, str] | None) -> None:
    """(Re)write the whole trace's legs in execution order, optionally with one leg
    carrying NO payload — the ADR-0027 D6 gap.

    Writing every leg, in order, is what P-interactions actually does when it
    re-derives a trace: each leg is rewritten in place and draws a fresh ``seq``
    from the sequence (migration 0009's default; 0010's NOTIFY trigger covers UPDATE
    for exactly this). So a re-derivation shifts every ``seq`` upward while keeping
    the relative order the traversal reads — which is why the tests below rewrite
    all six legs rather than poking one, and why the stale-row cleanup cannot key on
    ``seq`` thresholds.
    """
    with psycopg.connect(dsn) as conn:
        for original_seq, (ix_id, leg_type, payload_hash) in enumerate(
            _WORKED_EXAMPLE, start=1
        ):
            _leg(
                conn,
                ix_id=ix_id,
                leg_type=leg_type,
                payload_hash=None
                if unpayloaded == (ix_id, leg_type)
                else payload_hash,
                original_seq=original_seq,
            )
        conn.commit()


# The realistic trigger (ADR-0025: the request leg always exists), so the gap the
# tests induce is a missing mid-trace RESPONSE payload.
_GAP_LEG = ("ix_al1", "response")


def _seed_trace_with_gap(dsn: str, *, gap: bool) -> None:
    """The worked-example trace, six legs, with the first llm response's payload
    absent when *gap*."""
    with psycopg.connect(dsn) as conn:
        _seed_entities(conn)
        _interaction(conn, ix_id="ix_ua", trace_id=TRACE, caller="e_user", callee="e_agent")
        _interaction(conn, ix_id="ix_al1", trace_id=TRACE, caller="e_agent", callee="e_llm")
        _interaction(conn, ix_id="ix_al2", trace_id=TRACE, caller="e_agent", callee="e_llm")
        conn.commit()
    _write_legs(dsn, unpayloaded=_GAP_LEG if gap else None)


# --- readers ------------------------------------------------------------------


def _lineage_keys(dsn: str, trace_id: str = TRACE) -> set[tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT m.interaction_id, m.leg_type::text FROM lineage_metadata m "
            "JOIN interactions i ON i.id = m.interaction_id WHERE i.trace_id = %s",
            (trace_id,),
        ).fetchall()
    return {(r[0], r[1]) for r in rows}


def _status(dsn: str, trace_id: str = TRACE) -> tuple[str, int | None] | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT status::text, stopped_at_seq FROM lineage_trace_status "
            "WHERE trace_id = %s",
            (trace_id,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def _leg_seq(dsn: str, ix_id: str, leg_type: str) -> int:
    with psycopg.connect(dsn) as conn:
        (seq,) = conn.execute(
            "SELECT seq FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = %s",
            (ix_id, leg_type),
        ).fetchone()
    return seq


# --- a mid-trace missing response payload ------------------------------------


def test_gap_trace_persists_only_the_prefix(configured_db: str) -> None:
    """D6: legs before the gap keep lineage, legs at and after it get none."""
    _seed_trace_with_gap(configured_db, gap=True)
    driver.drain(0)

    assert _lineage_keys(configured_db) == {("ix_ua", "request"), ("ix_al1", "request")}


def test_gap_trace_persists_a_partial_status_with_the_stop_seq(
    configured_db: str,
) -> None:
    """The flag, and where it stopped — the leg ``seq`` of the payload-less
    response, read back from the legs table rather than assumed."""
    _seed_trace_with_gap(configured_db, gap=True)
    driver.drain(0)

    assert _status(configured_db) == (
        "partial",
        _leg_seq(configured_db, "ix_al1", "response"),
    )


def test_fully_payloaded_trace_persists_a_complete_status(configured_db: str) -> None:
    """The other half of the flag: every leg payloaded → ``complete``, no stop
    position, and lineage for all six legs."""
    _seed_trace_with_gap(configured_db, gap=False)
    driver.drain(0)

    assert len(_lineage_keys(configured_db)) == 6
    assert _status(configured_db) == ("complete", None)


def test_status_is_scoped_to_its_own_trace(configured_db: str) -> None:
    """``lineage_trace_status`` is keyed by trace, and the driver derives one
    trace per arriving leg — a gap in one trace must not mark another partial
    (a false claim about a trace nobody looked at)."""
    _seed_trace_with_gap(configured_db, gap=True)
    with psycopg.connect(configured_db) as conn:
        _entity(conn, eid="e_c", kind="client", natural_key="client:1.2.3.4")
        _entity(conn, eid="e_a2", kind="agent", natural_key="agent:(demo,other)")
        _interaction(conn, ix_id="ix_o", trace_id=OTHER_TRACE, caller="e_c", callee="e_a2")
        _leg(conn, ix_id="ix_o", leg_type="request", payload_hash="q1", original_seq=1)
        _leg(conn, ix_id="ix_o", leg_type="response", payload_hash="q2", original_seq=2)
        conn.commit()

    driver.drain(0)

    assert _status(configured_db, TRACE)[0] == "partial"
    assert _status(configured_db, OTHER_TRACE) == ("complete", None)


# --- re-derivation: the stale-row problem ------------------------------------


def test_redrive_of_a_gap_trace_is_idempotent(configured_db: str) -> None:
    """Resetting the cursor and re-draining converges — the prefix and the status
    are both a function of the trace, so a second full derivation must agree with
    the first."""
    _seed_trace_with_gap(configured_db, gap=True)
    driver.drain(0)
    first_keys, first_status = _lineage_keys(configured_db), _status(configured_db)

    with db.transaction() as tx:
        tx.execute(
            "UPDATE processor_state SET last_processed_seq = 0 "
            "WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        )
    driver.drain(0)

    assert _lineage_keys(configured_db) == first_keys
    assert _status(configured_db) == first_status


def test_a_gap_appearing_later_removes_the_lineage_it_invalidates(
    configured_db: str,
) -> None:
    """**The stale-row test.** A trace derives complete (six rows). Then the
    mid-trace response's payload is rewritten to NULL — P-interactions re-derives
    legs in place, which is why 0010's trigger covers UPDATE. The re-derivation's
    prefix is SHORTER than the persisted one, so the rows past the new cutoff must
    be DELETED. Upsert-only would leave lineage for legs after the gap while the
    status says partial — a self-contradictory read, and a claim about data whose
    provenance we can no longer see."""
    _seed_trace_with_gap(configured_db, gap=False)
    cursor = driver.drain(0)
    assert len(_lineage_keys(configured_db)) == 6, "sanity: derived complete first"

    _write_legs(configured_db, unpayloaded=_GAP_LEG)
    driver.drain(cursor)

    assert _lineage_keys(configured_db) == {("ix_ua", "request"), ("ix_al1", "request")}
    status, stopped_at = _status(configured_db)
    assert status == "partial"
    assert stopped_at == _leg_seq(configured_db, *_GAP_LEG)


def test_deleting_stale_rows_does_not_touch_another_trace(configured_db: str) -> None:
    """The delete is trace-scoped, like every other write on this path: it goes
    through ``interactions`` (``lineage_metadata`` has no ``trace_id``). A bug
    there would wipe an unrelated trace's lineage — a governance tool losing
    evidence it had."""
    _seed_trace_with_gap(configured_db, gap=False)
    with psycopg.connect(configured_db) as conn:
        _entity(conn, eid="e_c", kind="client", natural_key="client:1.2.3.4")
        _entity(conn, eid="e_a2", kind="agent", natural_key="agent:(demo,other)")
        _interaction(conn, ix_id="ix_o", trace_id=OTHER_TRACE, caller="e_c", callee="e_a2")
        _leg(conn, ix_id="ix_o", leg_type="request", payload_hash="q1", original_seq=1)
        _leg(conn, ix_id="ix_o", leg_type="response", payload_hash="q2", original_seq=2)
        conn.commit()
    cursor = driver.drain(0)
    assert len(_lineage_keys(configured_db, OTHER_TRACE)) == 2

    # Now truncate TRACE at its very first leg — the most destructive prefix.
    _write_legs(configured_db, unpayloaded=("ix_ua", "request"))
    driver.drain(cursor)

    assert _lineage_keys(configured_db, TRACE) == set()
    assert len(_lineage_keys(configured_db, OTHER_TRACE)) == 2
    assert _status(configured_db, OTHER_TRACE) == ("complete", None)


def test_partial_becomes_complete_when_the_late_payload_arrives(
    configured_db: str,
) -> None:
    """**The acceptance criterion.** A response payload is missing at first — the
    trace is partial and only its prefix has lineage. The payload lands (a late
    write, or a re-derivation that finally captured it) and the trace becomes
    whole: every leg gets lineage and the status flips to ``complete`` with the
    stop position cleared. The single trace-keyed status row is what makes this a
    plain overwrite with no stale ``partial`` left behind."""
    _seed_trace_with_gap(configured_db, gap=True)
    cursor = driver.drain(0)
    assert _status(configured_db)[0] == "partial"
    assert len(_lineage_keys(configured_db)) == 2

    _write_legs(configured_db, unpayloaded=None)  # the late payload lands
    driver.drain(cursor)

    assert _status(configured_db) == ("complete", None)
    assert len(_lineage_keys(configured_db)) == 6


def test_partial_to_complete_derives_the_full_lineage_not_just_the_rows(
    configured_db: str,
) -> None:
    """Row count alone would pass with six independently-derived origins. The
    trace becoming complete means the FLOW is visible again: the final response
    traces back to the user through both intermediate entities."""
    _seed_trace_with_gap(configured_db, gap=True)
    cursor = driver.drain(0)
    _write_legs(configured_db, unpayloaded=None)
    driver.drain(cursor)

    with psycopg.connect(configured_db) as conn:
        sources, entities = conn.execute(
            "SELECT data_sources, entities FROM lineage_metadata "
            "WHERE interaction_id = 'ix_ua' AND leg_type = 'response'"
        ).fetchone()
    assert sources == ["user:alice"]
    assert set(entities) == {"agent:(demo,advisor)", "llm:host/gpt"}


def test_a_trace_with_no_legs_writes_no_status(configured_db: str) -> None:
    """Pins the *absence* of a row as a deliberate output, not an oversight: the
    driver returns early for a trace with no legs, so it must not claim a status
    either (ADR-0027 D6 — absence is how *unknown* is said).

    Do not "strengthen" this into asserting ``complete``. The pure traversal does
    call an empty leg list complete, but the driver never gets there, and a
    ``complete`` row here would assert full coverage of a trace we have not seen.
    """
    _seed_trace_with_gap(configured_db, gap=False)
    driver.drain(0)

    assert _status(configured_db, "no-such-trace") is None
