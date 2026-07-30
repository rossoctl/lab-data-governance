"""Contiguous-prefix readiness-cursor primitive (issue #122, simplified for the
ADR-0027 reversal in issue #123).

These are pure-logic unit tests: no Postgres, no SQL, no processor loop. The
primitive under test — :mod:`data_governance.processors.readiness_cursor` — is
the one place the shared ``_driver.drain`` cannot be reused verbatim, because the
leg-readiness predicate is **non-monotonic in ``seq``**: a low-``seq`` leg whose
payload is not yet classified can sit behind a high-``seq`` ready leg.
``_driver.advance_cursor`` unconditionally advances to the max ``seq`` seen and
would strand the low-``seq`` leg forever. This helper instead advances only across
the leading unbroken run of *ready* items and stops at the first unready one
(contiguous prefix / head-of-line blocking).

**Reversal (issue #123):** legs now carry INDEPENDENT DB-owned ``seq``s (each from
``nextval('interaction_legs_seq')``, request leg inserted first → lower seq), so
``seq`` alone **totally orders** the legs. The composite ``(seq, leg_ordinal)``
watermark and the ``request < response`` ``leg_type`` tiebreaker the shared-seq
premise required are retired: the watermark is a plain single ``BIGINT`` that
round-trips through ``processor_state.last_processed_seq`` directly, and
request-before-response falls out of the seq order itself (the request leg simply
has the lower seq). ``ReadinessItem`` keeps ``leg_type`` for delivery/logging, but
it is no longer part of the sort key.

The tests feed the primitive already-fetched items carrying ``(seq, leg_type,
ready)`` and assert the deliverable prefix and the resulting plain-seq watermark.
#123 supplies the real ``interaction_legs`` / ``payload_classifications``-derived
items and readiness; the primitive stays table- and SQL-agnostic.
"""

from __future__ import annotations

from data_governance.processors import readiness_cursor as rc


def _item(seq: int, leg_type: str, ready: bool):
    """A readiness-stream item: its own DB-owned ``seq``, ``leg_type`` (carried for
    delivery/logging, not for ordering), and a boolean readiness flag (the
    classification-completion predicate #123 supplies)."""
    return rc.ReadinessItem(seq=seq, leg_type=leg_type, ready=ready)


def test_empty_input_delivers_nothing_and_holds_the_cursor() -> None:
    """No fetched items past the cursor → nothing delivered, watermark unchanged."""
    result = rc.contiguous_prefix([], cursor=0)

    assert result.delivered == []
    assert result.cursor == 0


def test_all_ready_run_is_delivered_whole_and_advances_to_the_last() -> None:
    """When every item is ready the whole run is delivered and the watermark
    advances to the highest (last) item's ``seq``."""
    items = [
        _item(1, "request", ready=True),
        _item(2, "response", ready=True),
        _item(3, "request", ready=True),
        _item(4, "response", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=0)

    assert [(it.seq, it.leg_type) for it in result.delivered] == [
        (1, "request"),
        (2, "response"),
        (3, "request"),
        (4, "response"),
    ]
    assert result.cursor == 4  # highest delivered seq


def test_stops_at_the_first_unready_item() -> None:
    """The prefix advances across the leading ready run and stops at the first
    unready item; that item and everything after it are NOT delivered, and the
    watermark stays at the last delivered seq."""
    items = [
        _item(1, "request", ready=True),
        _item(2, "response", ready=True),
        _item(3, "request", ready=False),  # first unready — stop here
        _item(4, "response", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=0)

    assert [it.seq for it in result.delivered] == [1, 2]
    assert result.cursor == 2  # last delivered seq, NOT jumped past the unready 3


def test_request_delivered_before_response_by_seq_order() -> None:
    """Within one interaction the ``request`` leg is delivered before the
    ``response`` leg because it has the lower DB-owned ``seq`` (request leg is
    inserted first, so ``nextval`` assigns it the lower value) — delivered in seq
    order even when fed response-first. No ``leg_type`` tiebreaker is involved."""
    # Adversarial input order: response (higher seq) fed BEFORE request (lower seq).
    items = [
        _item(8, "response", ready=True),
        _item(7, "request", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=0)

    assert [(it.seq, it.leg_type) for it in result.delivered] == [
        (7, "request"),  # lower seq first despite being fed second
        (8, "response"),
    ]
    assert result.cursor == 8


def test_head_of_line_blocking_holds_watermark_despite_a_later_ready_item() -> None:
    """One unready low-``seq`` item holds the watermark even though a higher-``seq``
    item is ready — the documented, intended head-of-line blocking behaviour (one
    slow classification holds the watermark). The ready high item is NOT delivered
    and the cursor is NOT advanced past the unready low item."""
    items = [
        _item(3, "request", ready=False),  # unready low seq — blocks everything
        _item(4, "response", ready=True),
        _item(9, "request", ready=True),  # ready, higher seq — must be HELD
    ]
    result = rc.contiguous_prefix(items, cursor=0)

    assert result.delivered == []  # nothing delivered — first item is unready
    assert result.cursor == 0  # watermark held at the resume point


def test_stranding_then_later_ready_advances_across_both_over_two_drains() -> None:
    """The core acceptance criterion. A ready item at a higher ``seq`` than an
    unready item does NOT advance the cursor past the unready one; when the low
    item LATER becomes ready, the next drain advances across BOTH.

    Simulates the two-drain sequence the #123 consumer runs: each drain re-fetches
    everything with ``seq > cursor`` and re-applies the primitive. The stranded
    low-``seq`` leg is re-fetched (unchanged cursor), so it is never skipped.
    """
    # Drain 1: seq=4 leg ready; seq=5 request UNREADY (its payload not yet
    # classified); a later seq=6 leg is ready but must be HELD behind seq=5.
    drain1_items = [
        _item(4, "request", ready=True),
        _item(5, "request", ready=False),  # stranded — low seq, unready
        _item(6, "response", ready=True),  # ready but higher seq → held
    ]
    r1 = rc.contiguous_prefix(drain1_items, cursor=0)

    assert [it.seq for it in r1.delivered] == [4]
    # The cursor stops at seq=4 — NOT jumped to the ready seq=6, so the unready
    # seq=5 leg is not stranded past.
    assert r1.cursor == 4

    # Between drains the seq=5 payload classifies → the leg becomes ready. The
    # consumer re-fetches WHERE seq > 4: seq=5 and seq=6 reappear.
    drain2_items = [
        _item(5, "request", ready=True),  # now ready
        _item(6, "response", ready=True),
    ]
    r2 = rc.contiguous_prefix(drain2_items, cursor=r1.cursor)

    assert [it.seq for it in r2.delivered] == [5, 6]
    assert r2.cursor == 6
    # The seq=4 leg from drain 1 is never re-delivered: drain 2 only saw items
    # with seq > 4, which is the consumer's re-fetch contract.


def test_all_unready_delivers_nothing_and_holds_cursor() -> None:
    """Every item unready → nothing delivered, watermark held (a whole batch of
    still-classifying legs blocks until the head classifies)."""
    items = [
        _item(1, "request", ready=False),
        _item(2, "response", ready=False),
    ]
    result = rc.contiguous_prefix(items, cursor=0)

    assert result.delivered == []
    assert result.cursor == 0


def test_first_item_unready_holds_even_from_a_nonzero_resume_cursor() -> None:
    """When resuming from a non-zero cursor and the first fetched item is unready,
    the watermark is held at the RESUME cursor (not reset to 0)."""
    items = [_item(21, "request", ready=False)]
    result = rc.contiguous_prefix(items, cursor=20)

    assert result.delivered == []
    assert result.cursor == 20


def test_cross_interaction_delivered_in_seq_order() -> None:
    """Across DIFFERENT interactions delivery follows the ``seq`` total order —
    each leg has a distinct seq, so two ready interactions' legs interleave purely
    by seq (request-before-response within each still holds because each request
    leg has the lower seq of its pair)."""
    # Two distinct interactions: A = (10 request, 12 response), B = (11 request,
    # 13 response), fed shuffled. All ready.
    items = [
        _item(13, "response", ready=True),
        _item(10, "request", ready=True),
        _item(12, "response", ready=True),
        _item(11, "request", ready=True),
    ]
    result = rc.contiguous_prefix(items, cursor=0)

    assert [it.seq for it in result.delivered] == [10, 11, 12, 13]
    assert result.cursor == 13


def test_delivered_seq_holds_the_cursor_at_the_last_delivered_leg() -> None:
    """A ``seq`` whose request leg (lower seq) is delivered but whose response leg
    (higher seq) is HELD (unready) advances the plain-seq cursor only to the
    delivered request leg's seq — NOT past the held response leg. On the next drain
    the consumer re-fetches ``seq > cursor``, so the still-unready response leg
    reappears and is re-evaluated rather than skipped."""
    items = [
        _item(30, "request", ready=True),  # delivered (lower seq)
        _item(31, "response", ready=False),  # HELD — same interaction, unready
    ]
    result = rc.contiguous_prefix(items, cursor=0)

    assert [(it.seq, it.leg_type) for it in result.delivered] == [(30, "request")]
    assert result.cursor == 30  # strictly below the held (31, response)
