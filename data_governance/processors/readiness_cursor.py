"""Contiguous-prefix readiness-cursor primitive (issue #122; simplified for the
ADR-0027 reversal in issue #123).

This is the **one place** the shared drain loop (:mod:`._driver`) cannot be
reused verbatim. ``_driver.drain`` advances its durable cursor to
``item_seq(item)`` for *every* fetched item unconditionally — it assumes every
item past the cursor is processable (monotonic in ``seq``). The leg-readiness
stream breaks that assumption: a leg's readiness (written **and** its payload, if
any, classified — the **Leg readiness** term in CONTEXT.md, ADR-0027) is
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

Total order — a plain single ``seq`` (the reversal)
---------------------------------------------------
Since the ADR-0027 reversal (issue #123) each **Interaction leg** carries its own
**distinct** DB-owned ``seq`` (``nextval('interaction_legs_seq')``; the request
leg is inserted first, so it gets the lower value). ``seq`` alone therefore
**totally orders** the legs — there is no shared-``seq`` collision to break, so
the old composite ``(seq, leg_ordinal)`` watermark and the ``request < response``
``leg_type`` tiebreaker are gone. Within one interaction the request leg is
delivered before the response leg simply because its ``seq`` is lower; across
different interactions delivery follows the same ``seq`` order. ``ReadinessItem``
still carries ``leg_type``, but only for delivery/logging — it is **not** part of
the sort key.

Cursor representation
---------------------
:func:`contiguous_prefix` returns the deliverable prefix and a single ``int``
watermark — the ``seq`` of the **last delivered item**, or the caller-supplied
resume ``cursor`` if nothing was delivered. This plain ``BIGINT`` persists
directly in ``processor_state.last_processed_seq`` (no composite mapping needed):
the next drain re-fetches items **strictly after** it (``WHERE seq > cursor``) and
re-applies this function; a still-unready item at the stop point is simply
re-fetched and re-evaluated next time, and advances once it becomes ready — no
re-delivery, no skip.

Table/SQL independence
----------------------
The primitive takes readiness as a boolean flag already computed on fetched
items — it touches no table and issues no SQL. #123 supplies the real
``interaction_legs`` / ``payload_classifications``-derived items and computes
``ready`` (``payload_hash IS NULL`` OR a ``payload_classifications`` row exists
for the content hash). This keeps the primitive unit-testable DB-free and reusable
by the readiness-drain consumer without a circular import.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class ReadinessItem:
    """One already-fetched item of the leg-readiness stream.

    Deliberately minimal and table-agnostic: it carries the leg's own distinct
    ``seq`` (the sole sort key since the ADR-0027 reversal), the ``leg_type`` (kept
    for delivery/logging — request-before-response is now implied by the lower
    request ``seq``, so ``leg_type`` no longer participates in ordering), and a
    ``ready`` boolean (the classification-completion predicate #123 computes). #123
    carries the full leg identity it needs to deliver downstream on its own richer
    item type; the primitive only reads these three fields.

    Attributes:
        seq: the leg's own DB-owned ``seq`` (``nextval('interaction_legs_seq')``).
            Distinct per leg — the request leg of an interaction has a lower seq
            than its response leg because it is inserted first.
        leg_type: ``"request"`` or ``"response"`` (delivery/logging only).
        ready: whether the leg is ready for a governance decision
            (``payload_hash IS NULL`` OR classified — computed by #123).
    """

    seq: int
    leg_type: str
    ready: bool


@dataclasses.dataclass(frozen=True)
class PrefixResult:
    """The outcome of one :func:`contiguous_prefix` evaluation.

    Attributes:
        delivered: the leading unbroken run of ready items, in ascending ``seq``
            order. Empty if the first item is unready (or there are no items).
        cursor: the plain ``seq`` watermark to resume strictly-after — the last
            delivered item's ``seq``, or the caller-supplied resume cursor if
            nothing was delivered (head-of-line blocking holds it in place).
            Persistable directly in ``processor_state.last_processed_seq``.
    """

    delivered: list[ReadinessItem]
    cursor: int


def contiguous_prefix(
    items: list[ReadinessItem],
    cursor: int,
) -> PrefixResult:
    """Advance across the leading unbroken run of ready items; stop at the first
    unready one.

    *items* are the already-fetched candidates past *cursor* (in any order — this
    function sorts them by ``seq`` itself, so a caller feeding them
    response-before-request still gets the lower-``seq`` request leg first).
    *cursor* is the plain ``seq`` watermark the consumer is resuming from.

    Returns a :class:`PrefixResult` with the deliverable prefix and the ``seq``
    watermark to persist. If the first item (in ``seq`` order) is unready, nothing
    is delivered and the cursor stays at *cursor* — **head-of-line blocking**: the
    unready item holds the watermark even if later items are ready, so the next
    drain re-evaluates it and advances only once it becomes ready.
    """
    ordered = sorted(items, key=lambda it: it.seq)

    delivered: list[ReadinessItem] = []
    for item in ordered:
        if not item.ready:
            break  # stop at the first unready item — the rest is held this drain.
        delivered.append(item)

    if not delivered:
        return PrefixResult(delivered=[], cursor=cursor)

    return PrefixResult(delivered=delivered, cursor=delivered[-1].seq)
