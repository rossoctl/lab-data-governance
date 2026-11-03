"""Per-trace writer lock shared by sidecar reconciliation and data lineage.

Two processors rewrite derived state for a whole trace in one transaction. The
sidecar reconciler (:func:`interactions.sidecar.derive_trace`) deletes the trace's
stale interactions and legs. The data-lineage derivation
(:func:`data_lineage.driver.process_leg`) reads every leg of the trace and writes
its ``lineage_metadata``. Migration 0026's delete triggers remove a deleted leg's
lineage row, so the two must not interleave:

- A lineage write that lands after the triggers ran would be a permanent orphan:
  ``_delete_stale`` reaches lineage rows only through ``interactions``.
- Row locks in each writer cannot keep the rows alive. The reconciler's multi-row
  DELETE locks rows in physical order and the reader locks them in key order, so
  either side can become a deadlock victim. Neither processor loop retries 40P01.

So each of the two takes :func:`lock_trace` before it reads or writes anything of
the trace, and holds it until commit. Whichever transaction gets the lock first
finishes with the trace before the other one reads it.

Rules for a caller:

- **Take it first, and for one trace only.** A transaction that waits for this lock
  must hold no other lock that its holder could need. The lock is the first
  statement of each per-item transaction (``_driver.drain`` and the lineage dirty
  and stale arms each open one transaction per trace), so a waiter holds nothing.
  A transaction that ever needed two traces would have to lock them in sorted
  order, or two such transactions could deadlock on each other.
- **Run at READ COMMITTED** (the pool default). Each later statement then takes its
  snapshot after the lock was granted, so it sees the other writer's committed
  changes. At REPEATABLE READ the snapshot would be taken by the lock statement
  itself, before the wait, and the guarantee would be lost.

The graph and streaming interactions algorithms (``state.flush``) only upsert
interactions and legs. Their update triggers queue the trace for re-derivation, so
they do not need this lock.
"""

from __future__ import annotations

from data_governance import db

__all__ = ["TRACE_LOCK_NAMESPACE", "lock_trace"]

# The first key of the two-int4 advisory lock: ASCII "dgtr" (data-governance
# trace). Postgres keeps two-int4 advisory keys apart from single-bigint ones
# (``pg_locks.objsubid`` 2 vs 1), so this lock cannot meet a bigint key such as
# ``hashtextextended(name, 0)``, whatever the values. Any other two-int4 advisory
# lock in this database must choose a different first key.
TRACE_LOCK_NAMESPACE = 0x64677472


def lock_trace(tx: db.Transaction, trace_id: str) -> None:
    """Hold the writer lock for *trace_id* until *tx* commits or rolls back.

    Blocks while another transaction holds it. The second key is
    ``hashtext(trace_id)``, so two different traces can share a lock. A collision
    only makes their writers wait for each other: the lock is never relied on to
    tell traces apart. The key is never stored, so a hash change in a later Postgres
    release would be harmless.
    """
    tx.execute(
        "SELECT pg_advisory_xact_lock(%s::integer, hashtext(%s))",
        (TRACE_LOCK_NAMESPACE, trace_id),
    )
