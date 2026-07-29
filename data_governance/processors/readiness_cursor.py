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

Cursor representation
---------------------
:func:`contiguous_prefix` returns the deliverable prefix and a single composite
:class:`Cursor` ``(seq, leg_ordinal)`` — the position of the **last delivered
item**, or the caller-supplied resume cursor if nothing was delivered. The next
drain re-fetches items **strictly after** that composite cursor and re-applies
this function; a still-unready item at the stop point is simply re-fetched and
re-evaluated next time, and advances once it becomes ready — no re-delivery, no
skip. :meth:`Cursor.resume_key` exposes the ``(seq, leg_ordinal)`` pair for the
re-fetch predicate.

How that composite position is persisted — the shared durable cursor
``processor_state.last_processed_seq`` is a single ``BIGINT`` — is a
consumer-side decision, not this primitive's. ADR-0027 discusses it: for the
current (Case-X streaming) source both legs share one ``seq`` written
all-or-nothing, so a ``seq`` is delivered atomically and ``cursor.seq`` round-trips
through the single BIGINT; the composite ``Cursor`` is the forward-compatibility
hook for the future Case-Y source, where the response leg becomes ready later and
the half-delivered case is real. This module surfaces the composite position and
leaves the persistence mapping to the consumer.
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

    def resume_key(self) -> tuple[int, int]:
        """The ``(seq, leg_ordinal)`` pair identifying this composite position.

        The consumer re-fetches everything **strictly after** this position and
        re-applies :func:`contiguous_prefix`. The SQL that expresses "strictly
        after" belongs to the consumer (#123): the schema stores ``leg_type`` as
        an ENUM, not an integer ordinal, so a row-value ``WHERE (seq, ...) > ...``
        needs the consumer to derive the ordinal (e.g. a ``CASE`` expression) — an
        SQL concern this table-agnostic primitive deliberately does not own.
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
    """

    delivered: list[ReadinessItem]
    cursor: Cursor


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
        return PrefixResult(delivered=[], cursor=cursor)

    return PrefixResult(delivered=delivered, cursor=Cursor.of(delivered[-1]))
