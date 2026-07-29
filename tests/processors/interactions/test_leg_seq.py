"""DB-owned ``interaction_legs.seq`` for the streaming algorithm (issue #123).

ADR-0027 reversal: the two legs of a streaming (Case-X) interaction no longer
share one span-derived ``seq``. Each leg gets its OWN DB-owned ``seq`` from
``nextval('interaction_legs_seq')`` (the column DEFAULT, migration 0009), so a
plain single-BIGINT ``seq`` watermark totally orders the legs and a leg-readiness
consumer can advance past a ready leg while a lower-``seq`` unready leg blocks it.

The invariants pinned here (all against the real streaming write path
``state.flush(legs_by_ix=None)`` driven end-to-end by the shared drain):

  1. every persisted leg has a distinct ``seq`` — no two legs share one anymore;
  2. within one interaction the ``request`` leg's ``seq`` is strictly lower than
     its ``response`` leg's (``_legs_of`` emits request first, so ``nextval``
     assigns it the lower value);
  3. distinct interactions get distinct, monotonically-increasing leg seqs
     (a proper cursorable stream);
  4. a re-derive (re-flush of the same trace) does NOT churn any leg's ``seq`` —
     ``seq`` was dropped from the ``ON CONFLICT ... DO UPDATE SET`` so the once-
     assigned value is preserved (cosmetic ``nextval`` gaps are allowed; the
     stored value is stable), preserving replay determinism / crash recovery.

The ``--scramble`` gate (``test_scramble.py``) already excludes ``seq`` /
``original_seq`` from its byte comparison of ``interaction_legs`` (they are
arrival-order-dependent passenger fields), so DB-owned seq introduces no new
invariant violation there — that gate keeps passing untouched.
"""

from __future__ import annotations

import psycopg

from .conftest import drain_all as _drain_all


def _legs(dsn: str) -> list[tuple]:
    """All persisted legs as ``(interaction_id, leg_type, seq)`` ordered by seq."""
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT interaction_id, leg_type::text, seq FROM interaction_legs "
            "ORDER BY seq"
        ).fetchall()


def test_streaming_legs_get_distinct_db_owned_seqs(loaded_trace: str) -> None:
    """After a full streaming drain every persisted leg has a distinct ``seq``.

    Under the old shared-seq model the two legs of one interaction shared
    ``ix.seq``; now each leg's ``seq`` comes from ``nextval`` so no two legs
    collide. This is invariant (1) — the prerequisite for a plain-seq total order.
    """
    _drain_all(loaded_trace)
    legs = _legs(loaded_trace)
    assert legs, "the streaming drain must persist some legs"
    seqs = [row[2] for row in legs]
    assert len(set(seqs)) == len(seqs), f"duplicate leg seq persisted: {seqs}"


def test_streaming_request_leg_seq_is_lower_than_its_response(
    loaded_trace: str,
) -> None:
    """Within one interaction the ``request`` leg's ``seq`` is strictly below its
    ``response`` leg's — ``_legs_of`` inserts request first, so ``nextval`` gives
    it the lower value. This is what makes request-before-response fall out of a
    plain ``ORDER BY seq`` (invariant (2)), retiring the ``leg_type`` tiebreaker.
    """
    _drain_all(loaded_trace)
    by_ix: dict[str, dict[str, int]] = {}
    for ix_id, leg_type, seq in _legs(loaded_trace):
        by_ix.setdefault(ix_id, {})[leg_type] = seq

    checked = 0
    for ix_id, legs in by_ix.items():
        if "request" in legs and "response" in legs:
            assert legs["request"] < legs["response"], (
                f"interaction {ix_id}: request seq {legs['request']} is not < "
                f"response seq {legs['response']}"
            )
            checked += 1
    assert checked > 0, "expected some interactions with both legs to check"


def test_distinct_interactions_get_monotonic_leg_seqs(loaded_trace: str) -> None:
    """The leg stream is a proper cursorable stream: distinct interactions get
    distinct, increasing seqs, so ``WHERE seq > cursor ORDER BY seq`` paginates
    them (invariant (3))."""
    _drain_all(loaded_trace)
    seqs = [row[2] for row in _legs(loaded_trace)]
    assert seqs == sorted(seqs), "legs must read back in ascending seq order"
    assert len(set(seqs)) == len(seqs), "leg seqs must be globally distinct"


def test_rederive_does_not_churn_leg_seq(loaded_trace: str) -> None:
    """A re-derive (second full drain from cursor 0 over the same trace) must NOT
    change any persisted leg's ``seq`` (invariant (4)).

    ``seq`` is dropped from the ``ON CONFLICT (interaction_id, leg_type) DO UPDATE
    SET``, so the ``ON CONFLICT`` path preserves the row's existing seq even though
    ``nextval`` is consumed for the (discarded) insert attempt. That preservation
    is what keeps a cursor-reset re-drain / crash recovery from reshuffling seqs a
    downstream consumer has already delivered.
    """
    _drain_all(loaded_trace)
    before = {(ix, lt): seq for ix, lt, seq in _legs(loaded_trace)}

    # Reset the P-interactions cursor and re-drain the identical trace.
    with psycopg.connect(loaded_trace) as conn:
        conn.execute(
            "UPDATE processor_state SET last_processed_seq = 0 "
            "WHERE processor_name = 'interactions'"
        )
        conn.commit()
    _drain_all(loaded_trace)

    after = {(ix, lt): seq for ix, lt, seq in _legs(loaded_trace)}
    assert after == before, (
        "a re-derive must preserve every leg's seq (dropped from DO UPDATE SET)"
    )
