"""The data-lineage processor's drain loop over the interaction-legs stream (#117).

P-data-lineage is a Layer-2 derived consumer: it drains ``interaction_legs`` by
``seq`` through the shared cursor loop (``processors/_driver.py``), and for each
arriving leg re-derives that leg's WHOLE trace's data lineage into
``lineage_metadata``. The trace-wholesale re-derivation is the
``interactions/graph_driver.py`` precedent — the loop's grain is one leg, the
algorithm's grain is one trace.

These run in-process against a migrated DB, mirroring
``tests/processors/classification/test_driver.py``.
"""

from __future__ import annotations

import psycopg

from data_governance import db
from data_governance.matching import MatchResult
from data_governance.processors.data_lineage import driver


# --- helpers -----------------------------------------------------------------


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
        (content_hash, '{}'),
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
    payload_hash: str,
) -> None:
    # `interaction_legs.original_seq` was dropped in 0011_drop_leg_original_seq
    # (issue #133): a leg's `seq` is DB-owned and never mutates on re-derive, so the
    # frozen-vs-mutating comparison the column existed for is inert for legs. The
    # sequence allocates `seq` in INSERT order, which is the only ordering these
    # tests ever relied on — callers pass legs in execution order (see below).
    # NOTE: `entities.original_seq` is NOT dropped and `_entity` still writes it.
    _payload(conn, payload_hash)
    conn.execute(
        "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
        "payload_hash, error) VALUES (%s, %s, now(), %s, false) "
        "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
        "payload_hash = EXCLUDED.payload_hash, seq = nextval('interaction_legs_seq')",
        (ix_id, leg_type, payload_hash),
    )


TRACE = "t" * 32


def _seed_agent_trace(dsn: str) -> None:
    """The spec's worked-example shape, written straight into the derived tables:
    user→agent, agent→llm twice, then the agent's answer back to the user."""
    with psycopg.connect(dsn) as conn:
        _entity(conn, eid="e_user", kind="user", natural_key="user:alice")
        _entity(conn, eid="e_agent", kind="agent", natural_key="agent:(demo,advisor)")
        _entity(conn, eid="e_llm", kind="llm", natural_key="llm:host/gpt")
        _interaction(conn, ix_id="ix_ua", trace_id=TRACE, caller="e_user", callee="e_agent")
        _interaction(conn, ix_id="ix_al1", trace_id=TRACE, caller="e_agent", callee="e_llm")
        _interaction(conn, ix_id="ix_al2", trace_id=TRACE, caller="e_agent", callee="e_llm")
        # Legs written in execution order, so the sequence-allocated `seq`
        # reproduces the spec's numbering 1..6.
        _leg(conn, ix_id="ix_ua", leg_type="request", payload_hash="p1")
        _leg(conn, ix_id="ix_al1", leg_type="request", payload_hash="p2")
        _leg(conn, ix_id="ix_al1", leg_type="response", payload_hash="p3")
        _leg(conn, ix_id="ix_al2", leg_type="request", payload_hash="p4")
        _leg(conn, ix_id="ix_al2", leg_type="response", payload_hash="p5")
        _leg(conn, ix_id="ix_ua", leg_type="response", payload_hash="p6")
        conn.commit()


def _rows(dsn: str) -> dict[tuple[str, str], dict]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT interaction_id, leg_type::text, data_sources, "
            "source_transformations, entities, payload_hash, seq "
            "FROM lineage_metadata"
        ).fetchall()
    return {
        (r[0], r[1]): {
            "data_sources": r[2],
            "source_transformations": r[3],
            "entities": r[4],
            "payload_hash": r[5],
            "seq": r[6],
        }
        for r in rows
    }


# --- drain -------------------------------------------------------------------


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    assert driver.drain(0) == 0
    assert _rows(configured_db) == {}


def test_drain_writes_one_row_per_leg_and_advances_the_cursor(configured_db: str) -> None:
    _seed_agent_trace(configured_db)

    with db.transaction() as tx:
        start = driver.read_cursor(tx)
    new_cursor = driver.drain(start)

    with db.transaction() as tx:
        max_leg_seq = tx.fetch_one("SELECT max(seq) FROM interaction_legs")[0]
    assert new_cursor == max_leg_seq

    rows = _rows(configured_db)
    assert len(rows) == 6, "one lineage row per interaction leg"
    assert set(rows) == {
        ("ix_ua", "request"),
        ("ix_al1", "request"),
        ("ix_al1", "response"),
        ("ix_al2", "request"),
        ("ix_al2", "response"),
        ("ix_ua", "response"),
    }


def test_drain_uses_its_own_cursor_row(configured_db: str) -> None:
    """Acceptance: "keyed off its own cursor". It must not share the
    ``interactions`` / ``classification`` cursor rows."""
    _seed_agent_trace(configured_db)
    driver.drain(0)

    with db.transaction() as tx:
        names = {
            r[0]
            for r in tx.fetch_all("SELECT processor_name FROM processor_state", ())
        }
    assert driver.PROCESSOR_NAME in names
    assert driver.PROCESSOR_NAME not in {"interactions", "classification"}


def test_persisted_metadata_matches_the_spec_worked_example(configured_db: str) -> None:
    """The trivial default matcher treats every structural edge as real flow, so
    every payload in the trace traces back to the user, and the final answer has
    passed through the agent and the LLM."""
    _seed_agent_trace(configured_db)
    driver.drain(0)
    rows = _rows(configured_db)

    assert rows[("ix_ua", "request")]["data_sources"] == ["user:alice"]
    assert rows[("ix_ua", "request")]["entities"] == []
    for key in rows:
        assert rows[key]["data_sources"] == ["user:alice"], key

    final = rows[("ix_ua", "response")]
    assert set(final["entities"]) == {"agent:(demo,advisor)", "llm:host/gpt"}
    # No transformation from the trivial matcher, so every source maps to [].
    assert final["source_transformations"] == {"user:alice": []}


def test_entities_are_persisted_sorted_as_a_serialization_detail(
    configured_db: str,
) -> None:
    """``entities`` is written SORTED — for byte-stable re-derivation only, the same
    reason ``source_transformations`` sorts its sets.

    The spec calls this element unordered and defers ordering to a future
    trace-derived API, so the sort must not be read as flow order. What it buys is
    that a re-derivation of identical lineage produces an identical row, which is
    what makes idempotency observable. Pinned here because the *absence* of a
    deliberate order is exactly the thing a later change could quietly break by
    persisting whatever order a set iteration happened to hand over."""
    _seed_agent_trace(configured_db)
    driver.drain(0)
    first = _rows(configured_db)

    values = first[("ix_ua", "response")]["entities"]
    assert values == sorted(values), "written sorted, so the bytes are stable"

    # Re-derive from scratch: same rows, byte-identical, no set-iteration wobble.
    driver.drain(0)
    assert _rows(configured_db) == first


def test_payload_hash_is_stored_per_leg(configured_db: str) -> None:
    """D5: the hash is a secondary index for the deferred reverse lookup, so each
    row records the hash of the payload whose lineage it describes."""
    _seed_agent_trace(configured_db)
    driver.drain(0)
    rows = _rows(configured_db)

    assert rows[("ix_ua", "request")]["payload_hash"] == "p1"
    assert rows[("ix_ua", "response")]["payload_hash"] == "p6"


def test_identical_payloads_at_different_positions_get_distinct_rows(
    configured_db: str,
) -> None:
    """The reason D5 keys on the leg, not the hash: an entity echoing its input
    verbatim yields two legs with the SAME ``payload_hash`` and completely
    different lineage. Keying on the hash would collide them into one row."""
    with psycopg.connect(configured_db) as conn:
        _entity(conn, eid="e_user", kind="user", natural_key="user:alice")
        _entity(conn, eid="e_agent", kind="agent", natural_key="agent:(demo,echo)")
        _interaction(conn, ix_id="ix", trace_id=TRACE, caller="e_user", callee="e_agent")
        _leg(conn, ix_id="ix", leg_type="request", payload_hash="same")
        _leg(conn, ix_id="ix", leg_type="response", payload_hash="same")
        conn.commit()

    driver.drain(0)
    rows = _rows(configured_db)

    assert len(rows) == 2
    assert rows[("ix", "request")]["payload_hash"] == "same"
    assert rows[("ix", "response")]["payload_hash"] == "same"
    # Same bytes, different lineage: the request originates at the user, the
    # response has passed through the agent.
    assert rows[("ix", "request")]["entities"] == []
    assert rows[("ix", "response")]["entities"] == ["agent:(demo,echo)"]


def test_redrive_of_a_trace_is_idempotent(configured_db: str) -> None:
    """Acceptance: "Re-deriving a trace is idempotent — row count and content
    converge, no duplicates". Every arriving leg re-derives the whole trace, so
    the first drain already re-derives six times; resetting the cursor and
    re-draining must still converge."""
    _seed_agent_trace(configured_db)
    driver.drain(0)
    first = _rows(configured_db)

    with db.transaction() as tx:
        tx.execute(
            "UPDATE processor_state SET last_processed_seq = 0 "
            "WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        )
    driver.drain(0)
    second = _rows(configured_db)

    assert len(first) == 6
    assert first == second


def test_partial_trace_converges_as_later_legs_arrive(configured_db: str) -> None:
    """The trace-wholesale re-derivation in action: draining after only the first
    two legs exist yields lineage for those two; the later legs then arrive and
    the whole trace is re-derived, upserting rather than duplicating."""
    with psycopg.connect(configured_db) as conn:
        _entity(conn, eid="e_user", kind="user", natural_key="user:alice")
        _entity(conn, eid="e_agent", kind="agent", natural_key="agent:(demo,advisor)")
        _entity(conn, eid="e_llm", kind="llm", natural_key="llm:host/gpt")
        _interaction(conn, ix_id="ix_ua", trace_id=TRACE, caller="e_user", callee="e_agent")
        _interaction(conn, ix_id="ix_al", trace_id=TRACE, caller="e_agent", callee="e_llm")
        _leg(conn, ix_id="ix_ua", leg_type="request", payload_hash="p1")
        _leg(conn, ix_id="ix_al", leg_type="request", payload_hash="p2")
        conn.commit()

    cursor = driver.drain(0)
    assert len(_rows(configured_db)) == 2

    with psycopg.connect(configured_db) as conn:
        _leg(conn, ix_id="ix_al", leg_type="response", payload_hash="p3")
        _leg(conn, ix_id="ix_ua", leg_type="response", payload_hash="p4")
        conn.commit()

    driver.drain(cursor)
    rows = _rows(configured_db)
    assert len(rows) == 4, "no duplicates from the re-derivation"
    assert set(rows[("ix_ua", "response")]["entities"]) == {
        "agent:(demo,advisor)",
        "llm:host/gpt",
    }


def test_rederivation_overwrites_stale_metadata(configured_db: str) -> None:
    """Idempotency by UPSERT, not by insert-if-absent: when a leg's payload is
    rewritten (P-interactions re-derives a trace in place — the reason migration
    0010's trigger covers UPDATE), the persisted lineage must follow it."""
    _seed_agent_trace(configured_db)
    driver.drain(0)

    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE lineage_metadata SET data_sources = ARRAY['stale'], "
            "entities = ARRAY['stale']"
        )
        # Re-announce the leg the way a P-interactions re-derivation would.
        _leg(conn, ix_id="ix_ua", leg_type="response", payload_hash="p6")
        conn.commit()

    driver.drain(0)
    rows = _rows(configured_db)
    assert all(r["data_sources"] == ["user:alice"] for r in rows.values())


def test_legs_of_other_traces_are_untouched(configured_db: str) -> None:
    """The re-derivation is trace-scoped: ``interaction_legs`` has no
    ``trace_id``, so the driver must join through ``interactions`` — a bug there
    would pull unrelated traces into one lineage graph."""
    _seed_agent_trace(configured_db)
    other = "o" * 32
    with psycopg.connect(configured_db) as conn:
        _entity(conn, eid="e_c", kind="client", natural_key="client:1.2.3.4")
        _entity(conn, eid="e_a2", kind="agent", natural_key="agent:(demo,other)")
        _interaction(conn, ix_id="ix_o", trace_id=other, caller="e_c", callee="e_a2")
        _leg(conn, ix_id="ix_o", leg_type="request", payload_hash="q1")
        conn.commit()

    driver.drain(0)
    rows = _rows(configured_db)

    assert rows[("ix_o", "request")]["data_sources"] == ["client:1.2.3.4"]
    # The other trace's user never leaks in, and vice versa.
    assert rows[("ix_ua", "request")]["data_sources"] == ["user:alice"]


def test_seq_is_the_legs_own_seq(configured_db: str) -> None:
    """The row carries "its own ``seq``" (the ticket's persistence bullet) so a
    downstream consumer can cursor the lineage table. It is sourced from the leg
    that the metadata describes."""
    _seed_agent_trace(configured_db)
    driver.drain(0)

    with psycopg.connect(configured_db) as conn:
        pairs = conn.execute(
            "SELECT lm.seq, l.seq FROM lineage_metadata lm "
            "JOIN interaction_legs l USING (interaction_id, leg_type)"
        ).fetchall()
    assert pairs, "sanity"
    assert all(lm_seq == leg_seq for lm_seq, leg_seq in pairs)


# --- matcher wiring ----------------------------------------------------------


def test_matcher_receives_payload_content_not_hashes(configured_db: str) -> None:
    """The spec's ``match(payload_a, payload_b)`` compares payload *content*. The
    default matcher reads neither argument, so nothing today would notice a hash
    being passed instead — pinned here so swapping in a real, content-reading
    matcher stays a configuration change rather than a code change."""
    seen: list[tuple[object, object]] = []

    def _recording(payload_a: object, payload_b: object, /) -> MatchResult:
        seen.append((payload_a, payload_b))
        return MatchResult(matched=True)

    with psycopg.connect(configured_db) as conn:
        _entity(conn, eid="e_user", kind="user", natural_key="user:alice")
        _entity(conn, eid="e_agent", kind="agent", natural_key="agent:(demo,a)")
        _interaction(conn, ix_id="ix", trace_id=TRACE, caller="e_user", callee="e_agent")
        _leg(conn, ix_id="ix", leg_type="request", payload_hash="h_in")
        _leg(conn, ix_id="ix", leg_type="response", payload_hash="h_out")
        # Real content behind those hashes, distinguishable from the hashes.
        conn.execute(
            "UPDATE interaction_payloads SET content = %s::jsonb "
            "WHERE content_hash = 'h_in'",
            ('{"prompt": "hello"}',),
        )
        conn.execute(
            "UPDATE interaction_payloads SET content = %s::jsonb "
            "WHERE content_hash = 'h_out'",
            ('{"answer": "world"}',),
        )
        conn.commit()

    driver.drain(0, matcher=_recording)

    assert seen, "the matcher must have been called"
    for payload_a, payload_b in seen:
        assert payload_a == {"prompt": "hello"}, payload_a
        assert payload_b == {"answer": "world"}, payload_b


def test_driver_resolves_its_matcher_through_get_matcher(
    configured_db: str, monkeypatch
) -> None:
    """Acceptance: "Lineage code does not reach into matcher internals". Swapping
    the configured matcher must change the persisted lineage with no code change
    — so the driver has to resolve it through ``matching.get_matcher``."""
    from data_governance import matching

    def _never(payload_a: object, payload_b: object, /) -> MatchResult:
        return MatchResult(matched=False)

    monkeypatch.setitem(matching.config._MATCHERS, "refuse", _never)
    monkeypatch.setenv(matching.MATCHER_ENV_VAR, "refuse")

    _seed_agent_trace(configured_db)
    driver.drain(0)
    rows = _rows(configured_db)

    # With no matches, every leg is its own origin (D3(2)).
    assert rows[("ix_ua", "response")]["data_sources"] == ["agent:(demo,advisor)"]
    assert rows[("ix_al1", "response")]["data_sources"] == ["llm:host/gpt"]
    assert all(r["entities"] == [] for r in rows.values())
