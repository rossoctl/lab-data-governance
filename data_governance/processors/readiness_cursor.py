"""Contiguous-prefix readiness-cursor primitive (issue #122, ADR-0027).

This is the **one place** the shared drain loop (:mod:`._driver`) cannot be
reused verbatim. ``_driver.drain`` advances its durable cursor to
``item_seq(item)`` for *every* fetched item unconditionally — it assumes every
item past the cursor is processable (monotonic in ``seq``). The leg-readiness
stream breaks that assumption: a leg's readiness (written **and** its payload,
if any, classified — the **Leg readiness** term in CONTEXT.md, ADR-0027) is
**non-monotonic in ``seq``**. A low-``seq`` leg whose payload is not yet
classified can sit behind a high-``seq`` ready leg, so advancing to the max
``seq`` seen would strand the low-``seq`` leg forever.

So this module is a pure **cursor-advance decision function**: given a
``seq``-ordered set of already-fetched items each carrying a readiness flag, it
advances the watermark **only across the leading unbroken run of ready items and
stops at the first unready one** (a contiguous prefix). Everything from the first
unready item onward is neither delivered nor advanced past *this drain* — even if
later items are ready. That is **head-of-line blocking**: one slow classification
holds the watermark, and it is the intended, documented behaviour (ADR-0027,
"Decision" → contiguous-prefix cursor bullet).

Total order — the ``(seq, leg_type)`` tiebreaker
------------------------------------------------
Items are totally ordered by the composite key ``(seq, leg_type)`` with
**request before response**. Within one interaction the two legs of the current
(streaming / Case-X) source **share one deterministic span-derived ``seq``**
(``processors/interactions/state.py::flush`` stamps ``ix.seq`` on *both* legs,
overriding the schema's ``nextval('interaction_legs_seq')`` default — this is
deliberate: it preserves the ``--scramble`` replay-determinism invariant, and is
exactly why ADR-0027 rejects per-leg ``nextval``). Because request and response
can share a ``seq``, ``seq`` alone is **not** a total order; the ``leg_type``
tiebreaker is load-bearing. The ``request < response`` order is encoded by an
**explicit ordinal map** (:data:`_LEG_ORDINAL`), NOT by relying on the Postgres
``leg_type`` ENUM declaration order — an intentional, documented choice so the
delivered ordering (request leg ready before response leg) cannot silently drift
if the ENUM is ever redeclared.

Cross-interaction there is **no** ordering guarantee: only intra-interaction
``request < response`` is promised. Different interactions are delivered in
``seq`` order relative to each other only incidentally (that *is* the composite
order), never as a contract between distinct interactions.

Table/SQL independence
----------------------
The primitive takes readiness as a boolean flag already computed on fetched
items — it touches no table and issues no SQL. #123 supplies the real
``interaction_legs`` / ``payload_classifications``-derived items and computes
``ready`` (``payload_hash IS NULL`` OR a ``payload_classifications`` row exists
for the content hash). This keeps the primitive unit-testable DB-free and reusable
by the future readiness-drain consumer without a circular import.

Cursor representation — what #123 must persist
----------------------------------------------
:func:`contiguous_prefix` returns both the deliverable prefix and a composite
:class:`Cursor` ``(seq, leg_ordinal)`` — the position of the **last delivered
item**, or the caller-supplied resume cursor if nothing was delivered. The next
drain should re-fetch items **strictly after** that composite cursor
(:meth:`Cursor.after_sql_tuple` gives the ``(seq, leg_ordinal)`` pair for a
``WHERE (seq, leg_ordinal) > (%s, %s)`` predicate) and re-apply this function; a
still-unready item at the stop point is simply re-fetched and re-evaluated next
time, and advances once it becomes ready — no re-delivery, no skip.

**Single-BIGINT ``processor_state`` caveat (a real finding for #123).** The
shared durable cursor ``processor_state.last_processed_seq`` is a *single*
``BIGINT`` and cannot faithfully hold a composite ``(seq, leg_type)`` watermark
whenever a single ``seq`` is only **half-delivered** — i.e. the request leg at
``seq=N`` is ready and delivered but the response leg at the same ``seq=N`` is
unready and held. The last-delivered position is then ``(N, request)``, which no
single BIGINT can distinguish from "``seq=N`` fully done":

* Persist ``N`` and re-fetch ``seq > N`` → the held ``(N, response)`` leg is
  **skipped forever** (the stranding bug this primitive exists to prevent).
* Persist ``N - 1`` and re-fetch ``seq > N-1`` → the already-delivered
  ``(N, request)`` leg is **re-delivered**.

For the current (Case-X streaming) source this half-delivered case does not
arise — both legs share one ``seq`` and are written in one transaction with the
same readiness at write time, so a ``seq`` is delivered all-or-nothing and the
conservative :attr:`PrefixResult.last_fully_delivered_seq` (highest ``seq`` all
of whose fetched legs were delivered) round-trips correctly through the single
BIGINT with plain ``seq > cursor`` re-fetch. The composite :class:`Cursor` is the
forward-compatibility hook for the future Case-Y source, where the response leg
becomes ready later and the half-delivered case is real; there, #123 must either
persist the composite ``(seq, leg_ordinal)`` (widen ``processor_state`` or resume
with a two-column predicate) or accept idempotent re-delivery. This module surfaces
both values and leaves that choice to #123; it does not, and cannot, paper over
the single-BIGINT limitation.
"""

from __future__ import annotations

import dataclasses

# Explicit request-before-response ordinal map. Intentionally NOT derived from
# the Postgres ``leg_type`` ENUM declaration order: the delivered ordering
# (request leg ready before response leg within one shared-``seq`` interaction)
# is a documented contract (ADR-0027), so it is pinned here rather than left to
# depend on how the ENUM happens to be declared. Any leg_type absent from this
# map is a programming error (an unknown leg kind) and raises on sort.
_LEG_ORDINAL: dict[str, int] = {"request": 0, "response": 1}


def leg_ordinal(leg_type: str) -> int:
    """The explicit sort ordinal for *leg_type* (``request`` < ``response``).

    Raises :class:`KeyError` for an unknown leg kind rather than silently
    ordering it — an unrecognised ``leg_type`` is a bug, not a runtime input.
    """
    return _LEG_ORDINAL[leg_type]


@dataclasses.dataclass(frozen=True)
class ReadinessItem:
    """One already-fetched item of the leg-readiness stream.

    Deliberately minimal and table-agnostic: it carries only what the
    cursor-advance decision needs — the shared span-derived ``seq``, the
    ``leg_type`` (for the ``(seq, leg_type)`` total order), and a ``ready``
    boolean (the classification-completion predicate #123 computes). #123 will
    carry the full leg identity it needs to deliver downstream on its own richer
    item type; the primitive only reads these three fields.

    Attributes:
        seq: the leg's ``seq``. Both legs of one current-source interaction share
            this value (``state.flush`` stamps ``ix.seq`` on both), which is why
            ``leg_type`` is the necessary tiebreaker.
        leg_type: ``"request"`` or ``"response"``.
        ready: whether the leg is ready for a governance decision
            (``payload_hash IS NULL`` OR classified — computed by #123).
    """

    seq: int
    leg_type: str
    ready: bool

    @property
    def sort_key(self) -> tuple[int, int]:
        """The ``(seq, leg_ordinal)`` total-order key — request before response."""
        return (self.seq, leg_ordinal(self.leg_type))


@dataclasses.dataclass(frozen=True, order=True)
class Cursor:
    """A composite ``(seq, leg_ordinal)`` watermark position.

    Ordered so ``>`` / ``<`` compare lexicographically on ``(seq, leg_ordinal)``,
    matching the item total order. :meth:`start` is the "before everything"
    sentinel a fresh consumer resumes from.
    """

    seq: int
    leg_ordinal: int

    @classmethod
    def start(cls) -> "Cursor":
        """The sentinel cursor before any item — ``(seq=0, leg_ordinal=0)``.

        The current-source seqs come from a sequence starting at 1, so ``(0, 0)``
        sorts strictly before every real item.
        """
        return cls(seq=0, leg_ordinal=0)

    @classmethod
    def of(cls, item: ReadinessItem) -> "Cursor":
        """The cursor position *at* an item — its ``(seq, leg_ordinal)``."""
        return cls(seq=item.seq, leg_ordinal=leg_ordinal(item.leg_type))

    def after_sql_tuple(self) -> tuple[int, int]:
        """The ``(seq, leg_ordinal)`` pair for a strict-after re-fetch predicate.

        #123 uses this in ``WHERE (seq, leg_ordinal) > (%s, %s)`` (row-value
        comparison) so the next drain re-fetches everything after the last
        delivered item and re-applies :func:`contiguous_prefix`.
        """
        return (self.seq, self.leg_ordinal)


@dataclasses.dataclass(frozen=True)
class PrefixResult:
    """The outcome of one :func:`contiguous_prefix` evaluation.

    Attributes:
        delivered: the leading unbroken run of ready items, in ``(seq, leg_type)``
            order. Empty if the first item is unready (or there are no items).
        cursor: the composite watermark to resume strictly-after — the last
            delivered item's :class:`Cursor`, or the caller-supplied resume cursor
            if nothing was delivered (head-of-line blocking holds it in place).
        last_fully_delivered_seq: the highest ``seq`` all of whose fetched items
            were delivered, or the resume ``cursor.seq`` if none. This is the
            **conservative single-BIGINT** value safe to persist in
            ``processor_state.last_processed_seq`` with a plain ``seq > cursor``
            re-fetch for the current all-or-nothing-per-``seq`` source. It never
            advances past a ``seq`` that is only half-delivered, so it cannot skip
            a held same-``seq`` leg (it re-delivers instead — see module docstring).
    """

    delivered: list[ReadinessItem]
    cursor: Cursor
    last_fully_delivered_seq: int


def contiguous_prefix(
    items: list[ReadinessItem],
    cursor: Cursor,
) -> PrefixResult:
    """Advance across the leading unbroken run of ready items; stop at the first
    unready one.

    *items* are the already-fetched candidates past *cursor* (in any order —
    this function sorts them by the ``(seq, leg_type)`` total order itself, so a
    caller feeding them response-before-request still gets request-first
    delivery). *cursor* is where the consumer is resuming from.

    Returns a :class:`PrefixResult` with the deliverable prefix and the watermark
    to persist. If the first item (in total order) is unready, nothing is
    delivered and the cursor stays at *cursor* — **head-of-line blocking**: the
    unready item holds the watermark even if later items are ready, so the next
    drain re-evaluates it and advances only once it becomes ready.
    """
    ordered = sorted(items, key=lambda it: it.sort_key)

    delivered: list[ReadinessItem] = []
    for item in ordered:
        if not item.ready:
            break  # stop at the first unready item — the rest is held this drain.
        delivered.append(item)

    if not delivered:
        return PrefixResult(
            delivered=[],
            cursor=cursor,
            last_fully_delivered_seq=cursor.seq,
        )

    new_cursor = Cursor.of(delivered[-1])

    # The conservative single-BIGINT watermark: the highest seq whose EVERY
    # fetched item was delivered. If the drain stopped partway through a seq (a
    # same-seq item was held), that seq is not "fully delivered", so we fall back
    # to the previous fully-delivered seq (or the resume cursor's seq). This never
    # advances past a half-delivered seq — see the module docstring's caveat.
    stopped_at_seq: int | None = None
    if len(delivered) < len(ordered):
        stopped_at_seq = ordered[len(delivered)].seq
    last_delivered_seq = delivered[-1].seq
    if stopped_at_seq == last_delivered_seq:
        # The stop happened mid-seq: the last delivered seq still has a held
        # sibling, so it is not fully delivered. Fall back to the seq before it.
        fully = max(
            (it.seq for it in delivered if it.seq < last_delivered_seq),
            default=cursor.seq,
        )
    else:
        fully = last_delivered_seq

    return PrefixResult(
        delivered=delivered,
        cursor=new_cursor,
        last_fully_delivered_seq=fully,
    )
