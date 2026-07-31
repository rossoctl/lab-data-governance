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
    """Idempotency by UPSERT, not by insert-if-absent: a re-derivation of a trace
    must overwrite whatever an earlier one wrote, rather than leaving the first
    answer pinned.

    Scoped deliberately to the UPSERT itself: it re-drains from cursor 0, so it says
    nothing about whether a rewritten leg is *reachable*. That is a separate
    question, and this test used to be read as covering it — it does not, which is
    how issue #137 stayed open. See
    ``test_a_rewritten_leg_is_rederived_from_the_real_cursor`` for the reachability
    half.
    """
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


# --- stale re-derivation (issue #137) ----------------------------------------
#
# The bug: P-interactions rewrites a leg in place and PRESERVES its `seq`
# (`interactions/state.py` omits `seq` from the upsert's DO UPDATE SET, for replay
# determinism). The lineage consumer drains `WHERE seq > cursor`. So a rewritten
# leg's seq sits BELOW the cursor, the NOTIFY wake finds nothing past it, and the
# lineage derived from the OLD payload survives while `lineage_trace_status` still
# says `complete` — a stale origin claim presented as authoritative.
#
# `_rewrite_leg_preserving_seq` reproduces the production write exactly. Note the
# module's own `_leg` helper does NOT: it bumps `seq = nextval(...)` on conflict,
# which hands the leg a seq above the cursor and hides the whole failure mode.


def _rewrite_leg_preserving_seq(
    dsn: str, *, ix_id: str, leg_type: str, payload_hash: str
) -> int:
    """Rewrite a leg's payload the way P-interactions re-derivation does — new
    ``payload_hash``, ``seq`` untouched. Returns the (unchanged) seq."""
    with psycopg.connect(dsn) as conn:
        _payload(conn, payload_hash)
        before = conn.execute(
            "SELECT seq FROM interaction_legs WHERE interaction_id = %s AND leg_type = %s",
            (ix_id, leg_type),
        ).fetchone()
        assert before is not None, "the leg must already exist to be rewritten"
        conn.execute(
            "UPDATE interaction_legs SET payload_hash = %s "
            "WHERE interaction_id = %s AND leg_type = %s",
            (payload_hash, ix_id, leg_type),
        )
        after = conn.execute(
            "SELECT seq FROM interaction_legs WHERE interaction_id = %s AND leg_type = %s",
            (ix_id, leg_type),
        ).fetchone()
        conn.commit()
    assert after is not None and after[0] == before[0], "seq must be preserved"
    return int(before[0])


def test_a_rewritten_leg_is_rederived_from_the_real_cursor(configured_db: str) -> None:
    """The #137 regression. Drain to completion, rewrite a leg in place preserving
    its seq, then drain again FROM THE CURSOR THE FIRST DRAIN RETURNED (not 0).

    Draining from 0 would mask the bug entirely — that is exactly why the
    pre-existing ``test_rederivation_overwrites_stale_metadata`` passed while the
    hole was open."""
    _seed_agent_trace(configured_db)
    cursor = driver.drain(0)
    assert cursor > 0, "sanity: the first drain must have consumed the trace"

    before = _rows(configured_db)[("ix_al1", "response")]
    assert before["payload_hash"] == "p3", "sanity"

    _rewrite_leg_preserving_seq(
        configured_db, ix_id="ix_al1", leg_type="response", payload_hash="p3_rewritten"
    )

    driver.drain(cursor)

    after = _rows(configured_db)[("ix_al1", "response")]
    assert after["payload_hash"] == "p3_rewritten", (
        "the stale row still carries the pre-rewrite payload hash: the rewritten "
        "leg was never re-derived"
    )


def test_the_stale_pass_does_not_move_the_cursor_backwards(configured_db: str) -> None:
    """A stale leg's seq is below the cursor by definition. Re-deriving it must not
    drag the durable cursor down to it — that would re-drain every leg in between
    on the next wake, unboundedly, and break the monotonic-advance invariant the
    other processors on the shared loop rely on."""
    _seed_agent_trace(configured_db)
    cursor = driver.drain(0)

    # Rewrite the trace's FIRST leg — the largest possible backwards jump.
    stale_seq = _rewrite_leg_preserving_seq(
        configured_db, ix_id="ix_ua", leg_type="request", payload_hash="p1_rewritten"
    )
    assert stale_seq < cursor, "sanity: the rewritten leg is behind the cursor"

    returned = driver.drain(cursor)

    assert returned == cursor, "the returned cursor must not regress"
    with psycopg.connect(configured_db) as conn:
        (durable,) = conn.execute(
            "SELECT last_processed_seq FROM processor_state WHERE processor_name = %s",
            (driver.PROCESSOR_NAME,),
        ).fetchone()
    assert durable == cursor, "the DURABLE cursor must not regress either"


def test_the_stale_pass_is_idempotent(configured_db: str) -> None:
    """Once the stale leg has been re-derived, its stored hash matches the leg's
    again, so a second drain has nothing to find. Pinned because the staleness
    predicate IS the durable state for this pass (there is no cursor protecting
    it): if re-deriving failed to clear the condition, every subsequent wake would
    redo the same work forever."""
    _seed_agent_trace(configured_db)
    cursor = driver.drain(0)
    _rewrite_leg_preserving_seq(
        configured_db, ix_id="ix_al1", leg_type="response", payload_hash="p3_rewritten"
    )
    driver.drain(cursor)
    first = _rows(configured_db)

    driver.drain(cursor)

    assert _rows(configured_db) == first, "a settled trace must re-derive to itself"


def test_a_rewritten_leg_refreshes_the_whole_traces_lineage(configured_db: str) -> None:
    """The stale pass re-derives the whole TRACE, not just the offending leg — the
    algorithm's grain is a trace (ADR-0027 D1), and a rewritten payload changes what
    every LATER leg of that trace inherits. Here the rewrite lands on the trace's
    first leg, so a downstream leg's row must be rewritten too."""
    _seed_agent_trace(configured_db)
    cursor = driver.drain(0)

    with psycopg.connect(configured_db) as conn:
        # Corrupt a DOWNSTREAM leg's stored lineage. It is not itself stale by the
        # hash predicate, so only a whole-trace re-derivation restores it.
        conn.execute(
            "UPDATE lineage_metadata SET data_sources = ARRAY['corrupt'] "
            "WHERE interaction_id = 'ix_ua' AND leg_type = 'response'"
        )
        conn.commit()

    _rewrite_leg_preserving_seq(
        configured_db, ix_id="ix_ua", leg_type="request", payload_hash="p1_rewritten"
    )
    driver.drain(cursor)

    rows = _rows(configured_db)
    assert rows[("ix_ua", "response")]["data_sources"] == ["user:alice"], (
        "the downstream leg was not refreshed, so the pass re-derived only the "
        "stale leg rather than its whole trace"
    )


def test_an_unchanged_trace_is_not_rederived(configured_db: str) -> None:
    """The stale pass must be driven by the hash predicate, not by "re-derive
    everything on every wake". A drained, unmodified trace is not stale, so a
    subsequent drain must leave it entirely alone."""
    _seed_agent_trace(configured_db)
    cursor = driver.drain(0)

    with psycopg.connect(configured_db) as conn:
        # A sentinel no legitimate derivation would produce. If the pass re-derives
        # indiscriminately it will be overwritten; it must survive.
        conn.execute(
            "UPDATE lineage_metadata SET data_sources = ARRAY['sentinel'] "
            "WHERE interaction_id = 'ix_al1' AND leg_type = 'response'"
        )
        conn.commit()

    driver.drain(cursor)

    rows = _rows(configured_db)
    assert rows[("ix_al1", "response")]["data_sources"] == ["sentinel"], (
        "an unchanged trace was re-derived: the pass is not gated on staleness"
    )


def test_a_stale_leg_in_another_trace_is_also_found(configured_db: str) -> None:
    """The stale pass is not scoped to one trace — it sweeps whatever is stale. Two
    traces, both drained, both rewritten: both must be refreshed in one drain."""
    other = "u" * 32
    _seed_agent_trace(configured_db)
    with psycopg.connect(configured_db) as conn:
        _entity(conn, eid="e_user2", kind="user", natural_key="user:bob")
        _entity(conn, eid="e_agent2", kind="agent", natural_key="agent:(demo,other)")
        _interaction(
            conn, ix_id="ix_o", trace_id=other, caller="e_user2", callee="e_agent2"
        )
        _leg(conn, ix_id="ix_o", leg_type="request", payload_hash="q1")
        _leg(conn, ix_id="ix_o", leg_type="response", payload_hash="q2")
        conn.commit()

    cursor = driver.drain(0)
    _rewrite_leg_preserving_seq(
        configured_db, ix_id="ix_al1", leg_type="response", payload_hash="p3_rewritten"
    )
    _rewrite_leg_preserving_seq(
        configured_db, ix_id="ix_o", leg_type="response", payload_hash="q2_rewritten"
    )

    driver.drain(cursor)

    rows = _rows(configured_db)
    assert rows[("ix_al1", "response")]["payload_hash"] == "p3_rewritten"
    assert rows[("ix_o", "response")]["payload_hash"] == "q2_rewritten"


def test_an_unconsumed_leg_is_left_to_the_cursor_arm(configured_db: str) -> None:
    """A leg with no lineage row is *unconsumed*, not stale. It is still ahead of the
    cursor, so the normal arm owns it — the staleness join is INNER precisely so the
    two arms cannot race for the same leg.

    Pinned because the tempting "fix" for an unconsumed leg is to outer-join
    ``lineage_metadata``, which would hand it to both arms at once.
    """
    _seed_agent_trace(configured_db)
    cursor = driver.drain(0)

    # A brand-new leg, never derived: no lineage_metadata row exists for it.
    with psycopg.connect(configured_db) as conn:
        _interaction(
            conn, ix_id="ix_late", trace_id=TRACE, caller="e_agent", callee="e_llm"
        )
        _leg(conn, ix_id="ix_late", leg_type="request", payload_hash="p7")
        conn.commit()

    # Ask the driver, not Postgres: restating the predicate here would pass even if
    # _fetch_stale_traces were deleted.
    with db.transaction() as tx:
        assert driver._fetch_stale_traces(tx, 500) == [], (
            "an unconsumed leg must not register as stale"
        )

    # The cursor arm still picks it up, because its seq is above the cursor.
    driver.drain(cursor)
    assert ("ix_late", "request") in _rows(configured_db)


def test_a_leg_that_LOSES_its_payload_shrinks_the_trace_in_one_pass(
    configured_db: str,
) -> None:
    """The seam between the new stale arm and D6/D9: a rewrite can set
    ``payload_hash`` to NULL (the payload became unavailable), which moves D6's
    absent-payload cutoff and makes the derivation SHORTER.

    All three consequences must land in a single drain — the predicate clears (so the
    pass converges rather than re-detecting forever), D9's delete removes the rows past
    the new gap, and the trace's status flips to ``partial``. ``IS DISTINCT FROM`` is
    what makes the NULL side detectable at all; a plain ``!=`` would be NULL-valued
    and this trace would never be revisited.
    """
    _seed_agent_trace(configured_db)
    cursor = driver.drain(0)
    assert len(_rows(configured_db)) == 6, "sanity: the whole trace derived"

    # The second leg of the trace loses its payload, the way a rewrite would.
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE interaction_legs SET payload_hash = NULL "
            "WHERE interaction_id = 'ix_al1' AND leg_type = 'request'"
        )
        conn.commit()

    driver.drain(cursor)

    # The predicate must be clear — otherwise every future wake redoes this work.
    with db.transaction() as tx:
        assert driver._fetch_stale_traces(tx, 500) == [], "the pass did not converge"

    rows = _rows(configured_db)
    assert len(rows) < 6, "D9's delete did not remove the rows past the new gap"
    with psycopg.connect(configured_db) as conn:
        status, stopped = conn.execute(
            "SELECT status::text, stopped_at_seq FROM lineage_trace_status "
            "WHERE trace_id = %s",
            (TRACE,),
        ).fetchone()
    assert status == "partial", "the coverage claim did not follow the shrink"
    assert stopped is not None
