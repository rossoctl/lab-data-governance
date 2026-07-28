"""NOTIFY trigger on interaction_legs writes: make the legs a notified stream (issue #115).

Migration 0009 gave ``interaction_legs`` the *cursorable* half of a stream — a
``seq BIGINT`` column fed by the dedicated ``interaction_legs_seq`` sequence plus
the ``interaction_legs_seq_idx`` cursor-pagination index (ADR-0007's shape, one
layer down). Nothing announced those writes, so a downstream lineage deriver
(ADR-0027) would have to poll blindly. This revision adds the missing half: a
``pg_notify`` on a dedicated channel, so the deriver becomes a straightforward
instance of the existing shared processor loop (``LISTEN`` for work, poll as the
backstop) rather than a bespoke poller — exactly what migration 0007 did for
``interaction_payloads`` and the P-classification processor.

Nothing about lineage itself ships here. This is the prefactor only.

What this revision adds — mirroring ``dg_notify_payloads()`` / 0005's
``dg_notify_spans()``:

- ``dg_notify_legs()`` — plpgsql, ``PERFORM pg_notify('dg_legs_inserted', '')``,
  ``RETURNS trigger`` returning ``NULL`` (the return value of an ``AFTER``
  statement-level trigger is ignored; ``NULL`` is the conventional body);
- ``dg_legs_notify`` — a **statement-level** ``AFTER INSERT OR UPDATE`` trigger
  on ``interaction_legs``.

**Why ``OR UPDATE`` — the one real difference from the payloads trigger.**
``interaction_payloads`` is content-addressed and insert-only
(``ON CONFLICT (content_hash) DO NOTHING``), so 0007's ``AFTER INSERT`` covers
every write that can ever change a row. Legs are not: the P-interactions flush
writes them with an **upsert** —
``INSERT ... ON CONFLICT (interaction_id, leg_type) DO UPDATE SET occurred_at,
payload_hash, error, seq`` (``processors/interactions/state.py:flush``) — because
re-deriving a trace rewrites that trace's legs in place (ADR-0025: the PK is
``(interaction_id, leg_type)``, at most one request + one response leg per
interaction, so a re-derivation cannot insert a second row). An
``ON CONFLICT DO UPDATE`` that lands on the update path fires **UPDATE**
triggers, not INSERT triggers. An ``AFTER INSERT``-only trigger would therefore
be silent for every re-derived trace, and the lineage deriver would never learn
that the legs it already consumed have changed underneath it. Covering both
events closes that gap: first derivation → INSERT → notify; re-derivation →
UPDATE → notify.

**Why statement-level**, following 0007 rather than 0006's row-level pair:

- The signal is "something happened"; the payload is deliberately empty. The
  consumer always re-drains ``WHERE seq > cursor`` from its durable cursor, so a
  payload would be ignored, and empty payloads let Postgres coalesce a burst of
  writes within a transaction into effectively one wake.
- 0006 moved *spans* to row-level triggers to **suppress** spurious wakes: the
  span upsert carries a ``DO UPDATE ... WHERE`` finalization predicate, so a
  duplicate re-ingest can write zero rows while the statement still runs, and
  the statement-level trigger woke the consumer for a write that stored nothing.
  The legs upsert has **no** ``WHERE`` predicate — a conflicting write always
  performs the update — so there is no no-op path to suppress and no
  ``INSERTED`` / ``FINALIZED`` / ``DUPLICATE`` distinction to draw. A leg flush
  writes one statement per leg today; a future batched writer would fire once
  per batch instead of once per row, which is strictly better at zero cost now.
- Similarly there is no ``WHEN (NEW.seq IS DISTINCT FROM OLD.seq)`` guard (0006's
  defensive extra on the spans finalization trigger). It would be wrong here:
  a re-derivation *is* meaningful work for the lineage consumer even in the
  degenerate case where the rewritten row lands on the same ``seq``, and a
  statement-level trigger has no ``NEW``/``OLD`` to test in any case.

What this revision deliberately does NOT add:

- **No ``seq``/sequence/index work.** All three already exist from 0009; this
  revision is notification-only. The downgrade must leave them intact.
- **No lineage table, processor, matcher or cursor row.** Scope is the
  notification (issue #115); the deriver that consumes it is the next ticket.
- **No change to the write path.** ``state.py:flush`` is untouched — the trigger
  lives in the database precisely so the producer stays unaware of its derived
  consumers (ADR-0007). Existing processors are unaffected: the extra
  ``pg_notify`` on a channel nobody listens to yet is a no-op.

Correctness does not depend on this trigger. As with spans and payloads, the
consumer's poll loop is the backstop — with the trigger absent or a notification
dropped the deriver still drains on the poll alone. This revision only removes
latency. The Layer-1 ``db.listen()`` helper consumers use is recorded in
ADR-0015.

Per ADR-0002/0005 the DDL is hand-written ``op.execute(...)`` — no SQLAlchemy ORM
models.

Revision ID: 0010_legs_notify_trigger
Revises: 0009_interaction_legs
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0010_legs_notify_trigger"
down_revision: Union[str, Sequence[str], None] = "0009_interaction_legs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The notify function: payload-less pg_notify on the agreed channel. Same
    # shape as dg_notify_spans() (0005) and dg_notify_payloads() (0007) — the
    # function RETURNS trigger and returns NULL.
    op.execute(
        """
        CREATE FUNCTION dg_notify_legs() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('dg_legs_inserted', '');
            RETURN NULL;
        END;
        $$
        """
    )
    # Statement-level AFTER INSERT **OR UPDATE**: one notification per write
    # statement, empty payload. The OR UPDATE is load-bearing — legs are written
    # with ON CONFLICT (interaction_id, leg_type) DO UPDATE, and a conflict that
    # lands on the update path fires UPDATE triggers, not INSERT triggers, so an
    # INSERT-only trigger would leave every re-derived trace unannounced.
    op.execute(
        """
        CREATE TRIGGER dg_legs_notify
        AFTER INSERT OR UPDATE ON interaction_legs
        FOR EACH STATEMENT
        EXECUTE FUNCTION dg_notify_legs()
        """
    )


def downgrade() -> None:
    # Drop the trigger before the function it calls. 0009's seq column, sequence
    # and cursor index are not this revision's to remove.
    op.execute("DROP TRIGGER IF EXISTS dg_legs_notify ON interaction_legs")
    op.execute("DROP FUNCTION IF EXISTS dg_notify_legs()")
