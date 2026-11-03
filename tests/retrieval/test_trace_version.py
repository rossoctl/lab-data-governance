"""``get_trace_version`` — the Trace version token (ADR-0034).

The token is compared for equality only, so the tests assert exactly that:
it is stable while nothing changes, and it changes on each kind of derived
write a live page cares about — a leg, an entity attribution, a lineage row,
a lineage status flip, an in-place correction of a row a re-derive rewrote
under its old ``seq`` — and never because of another trace, nor because a
re-derive wrote identical values back.
"""

from __future__ import annotations

import psycopg
import pytest

from data_governance import retrieval

_TID = "ver-trace"


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


def test_fresh_trace_is_all_zero_and_stable(configured_db: str) -> None:
    assert retrieval.get_trace_version(_TID) == "legs=0;es=0;dig=0;lin=0:0;st=@0"
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
    assert retrieval.get_trace_version(_TID) == before


def test_before_any_migration_the_token_is_all_zero(pg_dsn: str) -> None:
    """An unmigrated DB is never an error, like every other retrieval read."""
    from data_governance import db

    db.close_pool()
    db.configure(pg_dsn)
    try:
        assert retrieval.get_trace_version(_TID) == "legs=0;es=0;dig=0;lin=0:0;st=@0"
    finally:
        db.close_pool()


# ---------------------------------------------------------------------------
# In-place corrections (issue #279 review): a re-derive rewrites a trace's rows
# under their once-assigned leg ``seq``, so none of the high-water marks moves.
# The token must still change — the live page re-orders from exactly these
# columns — and must NOT change when a re-derive writes identical values back.
# ---------------------------------------------------------------------------


def _seed_two_interactions(conn) -> None:
    """``ix1`` (request + response) and an unparented ``ix2`` (request only),
    with a mix of NULL and non-NULL leg fields so each correction below has
    something to move from."""
    _seed_entity(conn, entity_id="e1", trace_id=_TID, span_id="s1")
    _seed_entity(conn, entity_id="e2", trace_id=_TID, span_id="s2")
    conn.execute(
        "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
        "caller_entity_id, callee_entity_id, summary) VALUES "
        "('ix1', %s, NULL, 'e1', 'e1', 'e1 → e1'), "
        "('ix2', %s, NULL, 'e1', 'e1', 'e1 → e1')",
        (_TID, _TID),
    )
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, content_kind, error) VALUES "
        "('ix1', 'request',  '2026-11-03 10:00:00.000001+00', NULL, NULL, NULL), "
        "('ix1', 'response', '2026-11-03 10:00:02+00', 'h-resp', 'a2a_message', false), "
        "('ix2', 'request',  '2026-11-03 10:00:01+00', 'h-req', "
        "  'tool_call_arguments', NULL)"
    )
    conn.commit()


_LEG = "WHERE interaction_id = %s AND leg_type = %s"

#: One in-place correction per input the causal order or the derived picture
#: reads, each written the way a re-derive writes it: an UPDATE of an existing
#: row, which leaves every leg ``seq`` and every row count where it was.
_CORRECTIONS = {
    "re-parent": (
        "UPDATE interactions SET parent_interaction_id = 'ix1' WHERE id = 'ix2'", ()),
    "caller": (
        "UPDATE interactions SET caller_entity_id = 'e2' WHERE id = 'ix2'", ()),
    "callee": (
        "UPDATE interactions SET callee_entity_id = 'e2' WHERE id = 'ix2'", ()),
    "occurred_at by one microsecond": (
        "UPDATE interaction_legs SET occurred_at = occurred_at + interval '1 microsecond' "
        + _LEG, ("ix2", "request")),
    "occurred_at to NULL": (
        "UPDATE interaction_legs SET occurred_at = NULL " + _LEG, ("ix2", "request")),
    "payload_hash": (
        "UPDATE interaction_legs SET payload_hash = 'h-other' " + _LEG,
        ("ix2", "request")),
    "payload_hash NULL to empty": (
        "UPDATE interaction_legs SET payload_hash = '' " + _LEG, ("ix1", "request")),
    "error": (
        "UPDATE interaction_legs SET error = true " + _LEG, ("ix1", "response")),
    "error NULL to false": (
        "UPDATE interaction_legs SET error = false " + _LEG, ("ix2", "request")),
    "content_kind": (
        "UPDATE interaction_legs SET content_kind = 'tool_result' " + _LEG,
        ("ix2", "request")),
    "content_kind NULL to empty": (
        "UPDATE interaction_legs SET content_kind = '' " + _LEG, ("ix1", "request")),
}


@pytest.mark.parametrize("correction", sorted(_CORRECTIONS))
def test_an_in_place_correction_changes_the_token(
    configured_db: str, raw_conn, correction: str
) -> None:
    _seed_two_interactions(raw_conn)
    before = retrieval.get_trace_version(_TID)
    sql, params = _CORRECTIONS[correction]
    cur = raw_conn.execute(sql, params)
    assert cur.rowcount == 1, "the correction must actually rewrite a row"
    raw_conn.commit()
    assert retrieval.get_trace_version(_TID) != before


def test_rewriting_identical_values_leaves_the_token_alone(
    configured_db: str, raw_conn
) -> None:
    """A re-derive that reaches the same answer rewrites every row with the
    values it already had — that must not read as a change, or every open page
    would re-fetch on every span of a long trace."""
    _seed_two_interactions(raw_conn)
    raw_conn.execute(
        "UPDATE interactions SET parent_interaction_id = 'ix1' WHERE id = 'ix2'"
    )
    raw_conn.commit()
    before = retrieval.get_trace_version(_TID)

    raw_conn.execute(
        "UPDATE interactions SET parent_interaction_id = parent_interaction_id, "
        "caller_entity_id = caller_entity_id, callee_entity_id = callee_entity_id, "
        "summary = summary WHERE trace_id = %s",
        (_TID,),
    )
    raw_conn.commit()
    assert retrieval.get_trace_version(_TID) == before

    # The writers' own shape: the leg upsert, landing on DO UPDATE with the
    # values the row already holds (its seq untouched, as on every re-derive).
    rows = raw_conn.execute(
        "SELECT interaction_id, leg_type::text, occurred_at, payload_hash, "
        "content_kind, error FROM interaction_legs"
    ).fetchall()
    for row in rows:
        raw_conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, content_kind, error) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
            "occurred_at = EXCLUDED.occurred_at, payload_hash = EXCLUDED.payload_hash, "
            "content_kind = EXCLUDED.content_kind, error = EXCLUDED.error",
            row,
        )
    raw_conn.commit()
    assert retrieval.get_trace_version(_TID) == before


def test_the_token_does_not_depend_on_session_settings(configured_db: str, raw_conn) -> None:
    """Timestamps go into the token in one fixed form, so two API processes
    whose sessions differ in TimeZone or DateStyle hand out the same token."""
    from urllib.parse import quote

    from data_governance import db

    _seed_two_interactions(raw_conn)
    before = retrieval.get_trace_version(_TID)
    options = quote("-c TimeZone=Pacific/Chatham -c DateStyle=SQL,DMY")
    db.close_pool()
    db.configure(f"{configured_db}?options={options}")
    with db.transaction() as tx:
        row = tx.fetch_one("SELECT current_setting('TimeZone')")
    assert row is not None and row[0] == "Pacific/Chatham"
    assert retrieval.get_trace_version(_TID) == before


def test_another_traces_correction_does_not_change_the_token(
    configured_db: str, raw_conn
) -> None:
    _seed_two_interactions(raw_conn)
    _seed_entity(raw_conn, entity_id="e9", trace_id="elsewhere", span_id="s9")
    _add_interaction(raw_conn, ix_id="ix-else", trace_id="elsewhere", entity_id="e9")
    before = retrieval.get_trace_version(_TID)
    raw_conn.execute(
        "UPDATE interactions SET parent_interaction_id = 'ix1' WHERE id = 'ix-else'"
    )
    raw_conn.execute(
        "UPDATE interaction_legs SET occurred_at = NULL WHERE interaction_id = 'ix-else'"
    )
    raw_conn.commit()
    assert retrieval.get_trace_version(_TID) == before


def test_a_lineage_rewrite_under_the_same_seq_changes_the_token(
    configured_db: str, raw_conn
) -> None:
    """The lineage processor re-derives a corrected trace and upserts each row
    under the leg's preserved ``seq`` (``data_lineage/driver.py:_upsert``), with
    the coverage status unchanged. The interactions-side correction already
    moved the token, but the page may have re-read lineage before this write
    landed — so the rewrite must move the token again on its own."""
    _seed_two_interactions(raw_conn)
    raw_conn.execute(
        "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
        "entities, payload_hash, seq) VALUES ('ix2', 'request', '{a}', '{e1}', 'h-req', 3)"
    )
    raw_conn.execute(
        "INSERT INTO lineage_trace_status (trace_id, status) VALUES (%s, 'complete')",
        (_TID,),
    )
    raw_conn.commit()
    before = retrieval.get_trace_version(_TID)
    raw_conn.execute(
        "UPDATE lineage_metadata SET data_sources = '{a,b}', entities = '{e1,e2}' "
        "WHERE interaction_id = 'ix2'"
    )
    raw_conn.commit()
    assert retrieval.get_trace_version(_TID) != before
    raw_conn.execute(
        "UPDATE lineage_metadata SET data_sources = data_sources, entities = entities"
    )
    raw_conn.commit()
    after = retrieval.get_trace_version(_TID)
    raw_conn.execute("UPDATE lineage_metadata SET data_sources = '{a,b}'")
    raw_conn.commit()
    assert retrieval.get_trace_version(_TID) == after


def test_before_the_leg_content_kind_migration_the_token_still_works(
    pg_dsn: str, monkeypatch
) -> None:
    """``interaction_legs.content_kind`` arrives in 0022, after the table. A DB
    between the two (an API rolled out ahead of its migration) must still get
    a token, and it must still see a correction."""
    from alembic import command

    from data_governance import db
    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    command.upgrade(_alembic_config(), "0020_entity_namespace")
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES "
            "('ix1', %s, 'e1', 'e1', 's'), ('ix2', %s, 'e1', 'e1', 's')",
            (_TID, _TID),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at) "
            "VALUES ('ix1', 'request', now()), ('ix2', 'request', now())"
        )
        conn.commit()
        db.close_pool()
        db.configure(pg_dsn)
        try:
            before = retrieval.get_trace_version(_TID)
            conn.execute(
                "UPDATE interactions SET parent_interaction_id = 'ix1' WHERE id = 'ix2'"
            )
            conn.commit()
            assert retrieval.get_trace_version(_TID) != before
        finally:
            db.close_pool()


@pytest.mark.parametrize("revision", ["0013_lineage_metadata", "0014_lineage_trace_status"])
def test_before_the_lineage_entities_rename_the_token_still_works(
    pg_dsn: str, monkeypatch, revision: str
) -> None:
    """``lineage_metadata`` arrives in 0013 with the entity set named
    ``entity_path``; 0015 renames it ``entities``. A DB between the two must
    still get a token, and a rewrite of that set must still move it."""
    from alembic import command

    from data_governance import db
    from data_governance.db.migrate import _alembic_config

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    command.upgrade(_alembic_config(), revision)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES ('ix1', %s, 'e1', 'e1', 's')",
            (_TID,),
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at) "
            "VALUES ('ix1', 'request', now())"
        )
        conn.execute(
            "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
            "entity_path, seq) VALUES ('ix1', 'request', '{a}', '{e1}', 1)"
        )
        conn.commit()
        db.close_pool()
        db.configure(pg_dsn)
        try:
            before = retrieval.get_trace_version(_TID)
            conn.execute("UPDATE lineage_metadata SET entity_path = '{e1,e2}'")
            conn.commit()
            assert retrieval.get_trace_version(_TID) != before
        finally:
            db.close_pool()
