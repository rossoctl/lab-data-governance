"""Contiguous-prefix readiness-cursor primitive (issue #122, ADR-0027).

These are pure-logic unit tests: no Postgres, no SQL, no processor loop. The
primitive under test — :mod:`data_governance.processors.readiness_cursor` — is
the one place the shared ``_driver.drain`` cannot be reused verbatim, because
the leg-readiness predicate is **non-monotonic in ``seq``**: a low-``seq`` leg
whose payload is not yet classified can sit behind a high-``seq`` ready leg.
``_driver.advance_cursor`` unconditionally advances to the max ``seq`` seen and
would strand the low-``seq`` leg forever. This helper instead advances only
across the leading unbroken run of *ready* items and stops at the first unready
one (contiguous prefix / head-of-line blocking).

It also encodes the intra-interaction ordering: items are totally ordered by the
composite ``(seq, leg_type)`` with **request before response** via an explicit
ordinal map (NOT an incidental reliance on the Postgres ENUM declaration order),
so within one interaction — whose two legs share one deterministic span-derived
``seq`` (``state.flush`` stamps ``ix.seq`` on both) — the request leg is
delivered ready before the response leg.

The tests feed the primitive already-fetched items carrying ``(seq, leg_type,
ready)`` and assert the deliverable prefix and the resulting watermark. #123
supplies the real ``interaction_legs`` / ``payload_classifications``-derived
items and readiness; the primitive stays table- and SQL-agnostic.
"""

from __future__ import annotations

from data_governance.processors import readiness_cursor as rc


def _item(seq: int, leg_type: str, ready: bool):
    """A readiness-stream item: shared span-derived ``seq``, ``leg_type``, and a
    boolean readiness flag (the classification-completion predicate #123 supplies).
    """
    return rc.ReadinessItem(seq=seq, leg_type=leg_type, ready=ready)


def test_empty_input_delivers_nothing_and_holds_the_cursor() -> None:
    """No fetched items past the cursor → nothing delivered, watermark unchanged."""
    result = rc.contiguous_prefix([], cursor=rc.Cursor.start())

    assert result.delivered == []
    assert result.cursor == rc.Cursor.start()


def test_all_ready_run_is_delivered_whole_and_advances_to_the_last() -> None:
    """When every item is ready the whole run is delivered and the watermark
    advances to the last item (in ``(seq, leg_type)`` order)."""
    items = [
        _item(1, "request", ready=True),
        _item(1, "response", ready=True),
        _item(2, "request", ready=True),
        _item(2, "response", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=rc.Cursor.start())

    assert [(it.seq, it.leg_type) for it in result.delivered] == [
        (1, "request"),
        (1, "response"),
        (2, "request"),
        (2, "response"),
    ]
    assert result.cursor == rc.Cursor.of(items[3])  # (seq=2, response)
    # Both seqs fully delivered → the conservative single-BIGINT watermark is 2.
    assert result.last_fully_delivered_seq == 2


def test_stops_at_the_first_unready_item() -> None:
    """The prefix advances across the leading ready run and stops at the first
    unready item; that item and everything after it are NOT delivered."""
    items = [
        _item(1, "request", ready=True),
        _item(1, "response", ready=True),
        _item(2, "request", ready=False),  # first unready — stop here
        _item(2, "response", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=rc.Cursor.start())

    assert [(it.seq, it.leg_type) for it in result.delivered] == [
        (1, "request"),
        (1, "response"),
    ]
    assert result.cursor == rc.Cursor.of(items[1])  # (seq=1, response)
    assert result.last_fully_delivered_seq == 1


def test_request_delivered_before_response_via_explicit_tiebreaker() -> None:
    """Within one interaction (shared ``seq``) the ``request`` leg is delivered
    before the ``response`` leg — even when fed response-first. The order comes
    from the explicit ``(seq, leg_type)`` tiebreaker, not input order."""
    # Adversarial input order: response BEFORE request, same seq.
    items = [
        _item(7, "response", ready=True),
        _item(7, "request", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=rc.Cursor.start())

    assert [(it.seq, it.leg_type) for it in result.delivered] == [
        (7, "request"),  # request first despite being fed second
        (7, "response"),
    ]


def test_tiebreaker_is_explicit_not_incidental_enum_order() -> None:
    """The request-before-response order is pinned by an explicit ordinal map,
    documented as intentional (ADR-0027) rather than a reliance on ENUM order."""
    assert rc.leg_ordinal("request") < rc.leg_ordinal("response")


def test_head_of_line_blocking_holds_watermark_despite_a_later_ready_item() -> None:
    """One unready low-``seq`` item holds the watermark even though a higher-``seq``
    item is ready — the documented, intended head-of-line blocking behaviour
    (one slow classification holds the watermark). The ready high item is NOT
    delivered and the cursor is NOT advanced past the unready low item."""
    items = [
        _item(3, "request", ready=False),  # unready low seq — blocks everything
        _item(3, "response", ready=True),
        _item(9, "request", ready=True),  # ready, higher seq — must be HELD
    ]
    result = rc.contiguous_prefix(items, cursor=rc.Cursor.start())

    assert result.delivered == []  # nothing delivered — first item is unready
    assert result.cursor == rc.Cursor.start()  # watermark held at the resume point
    assert result.last_fully_delivered_seq == 0


def test_stranding_then_later_ready_advances_across_both_over_two_drains() -> None:
    """The core acceptance criterion. A ready item at a higher ``seq`` than an
    unready item does NOT advance the cursor past the unready one; when the low
    item LATER becomes ready, the next drain advances across BOTH.

    Simulates the two-drain sequence the #123 consumer will run: each drain
    re-fetches everything strictly after the persisted cursor and re-applies the
    primitive. The stranded low-``seq`` leg is re-fetched (unchanged cursor), so
    it is never skipped.
    """
    # Drain 1: seq=4 legs ready; seq=5 request UNREADY (its payload not yet
    # classified); a later seq=6 leg is ready but must be HELD behind seq=5.
    drain1_items = [
        _item(4, "request", ready=True),
        _item(4, "response", ready=True),
        _item(5, "request", ready=False),  # stranded — low seq, unready
        _item(6, "request", ready=True),  # ready but higher seq → held
    ]
    r1 = rc.contiguous_prefix(drain1_items, cursor=rc.Cursor.start())

    assert [(it.seq, it.leg_type) for it in r1.delivered] == [
        (4, "request"),
        (4, "response"),
    ]
    assert r1.cursor == rc.Cursor(seq=4, leg_ordinal=rc.leg_ordinal("response"))
    assert r1.last_fully_delivered_seq == 4  # seq=6 NOT jumped to — no stranding

    # Between drains the seq=5 payload classifies → the leg becomes ready. The
    # consumer re-fetches strictly after r1.cursor: seq=5 and seq=6 reappear.
    drain2_items = [
        _item(5, "request", ready=True),  # now ready
        _item(6, "request", ready=True),
    ]
    r2 = rc.contiguous_prefix(drain2_items, cursor=r1.cursor)

    assert [(it.seq, it.leg_type) for it in r2.delivered] == [
        (5, "request"),
        (6, "request"),
    ]
    assert r2.cursor == rc.Cursor(seq=6, leg_ordinal=rc.leg_ordinal("request"))
    # The seq=4 legs from drain 1 are never re-delivered: drain 2 only saw items
    # strictly after r1.cursor, which is the consumer's re-fetch contract.
    assert r2.last_fully_delivered_seq == 6


def test_cross_interaction_no_order_beyond_seq_between_distinct_interactions() -> None:
    """Across DIFFERENT interactions no ordering is promised beyond the ``seq``
    total order itself — only intra-interaction ``request < response`` is a
    contract. Two ready interactions at different seqs are delivered in seq order
    (which is all the composite order says), regardless of each other."""
    # Two distinct interactions, each with both legs ready, at seqs 10 and 11.
    items = [
        _item(11, "request", ready=True),
        _item(11, "response", ready=True),
        _item(10, "request", ready=True),
        _item(10, "response", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=rc.Cursor.start())

    # Delivered in seq order; within each interaction request precedes response.
    assert [(it.seq, it.leg_type) for it in result.delivered] == [
        (10, "request"),
        (10, "response"),
        (11, "request"),
        (11, "response"),
    ]


def test_all_unready_delivers_nothing_and_holds_cursor() -> None:
    """Every item unready → nothing delivered, watermark held (a whole batch of
    still-classifying legs blocks until the head classifies)."""
    items = [
        _item(1, "request", ready=False),
        _item(2, "request", ready=False),
    ]
    result = rc.contiguous_prefix(items, cursor=rc.Cursor.start())

    assert result.delivered == []
    assert result.cursor == rc.Cursor.start()
    assert result.last_fully_delivered_seq == 0


def test_first_item_unready_holds_even_from_a_nonzero_resume_cursor() -> None:
    """When resuming from a non-zero cursor and the first fetched item is unready,
    the watermark is held at the RESUME cursor (not reset to start)."""
    resume = rc.Cursor(seq=20, leg_ordinal=rc.leg_ordinal("response"))
    items = [_item(21, "request", ready=False)]
    result = rc.contiguous_prefix(items, cursor=resume)

    assert result.delivered == []
    assert result.cursor == resume
    assert result.last_fully_delivered_seq == 20


def test_half_delivered_seq_is_conservative_in_single_bigint_watermark() -> None:
    """A ``seq`` whose request leg is delivered but whose response leg is HELD
    (unready, same ``seq``) is NOT counted as fully delivered: the conservative
    single-BIGINT ``last_fully_delivered_seq`` stays below it, so a plain
    ``seq > cursor`` re-fetch re-fetches the held same-``seq`` leg rather than
    skipping it. This is the documented single-BIGINT caveat for the future
    Case-Y source (the composite ``cursor`` is the faithful watermark)."""
    items = [
        _item(30, "request", ready=True),  # delivered
        _item(30, "response", ready=False),  # HELD — same seq, unready
    ]
    result = rc.contiguous_prefix(items, cursor=rc.Cursor.start())

    # The composite cursor faithfully records (30, request) as last delivered.
    assert result.cursor == rc.Cursor(seq=30, leg_ordinal=rc.leg_ordinal("request"))
    # But the single-BIGINT watermark must NOT reach 30 (that would let a
    # `seq > 30` re-fetch skip the held (30, response) leg). It stays conservative.
    assert result.last_fully_delivered_seq == 0
