"""The per-trace writer lock shared by sidecar reconciliation and data lineage.

``lock_trace`` takes a transaction-scoped advisory lock on a two-int4 key: a fixed
namespace plus ``hashtext(trace_id)``. These pin what callers rely on: one trace
excludes itself until commit, another trace is not held up, and the key cannot
meet the single-bigint advisory keys used elsewhere.
"""

from __future__ import annotations

import psycopg

from data_governance import db
from data_governance.processors.trace_lock import TRACE_LOCK_NAMESPACE, lock_trace

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
OTHER = "88888888888888888888888888888888"


def _try_trace(conn: psycopg.Connection, trace_id: str) -> bool:
    return conn.execute(
        "SELECT pg_try_advisory_xact_lock(%s::integer, hashtext(%s))",
        (TRACE_LOCK_NAMESPACE, trace_id),
    ).fetchone()[0]


def test_a_trace_lock_excludes_the_same_trace_until_commit(pg_dsn: str) -> None:
    with psycopg.connect(pg_dsn) as holder, psycopg.connect(pg_dsn) as other:
        lock_trace(db.Transaction(holder), TRACE)
        assert _try_trace(other, TRACE) is False
        assert _try_trace(other, OTHER) is True
        other.rollback()
        holder.commit()
        assert _try_trace(other, TRACE) is True


def test_the_lock_is_a_two_int_key_in_its_own_namespace(pg_dsn: str) -> None:
    with psycopg.connect(pg_dsn) as holder, psycopg.connect(pg_dsn) as other:
        lock_trace(db.Transaction(holder), TRACE)
        (key2,) = holder.execute("SELECT hashtext(%s)", (TRACE,)).fetchone()
        rows = other.execute(
            "SELECT classid::bigint, objid::bigint, objsubid FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = %s",
            (holder.info.backend_pid,),
        ).fetchall()
        # objsubid 2 marks a two-int4 key: classid is key1, objid is key2's bits.
        assert rows == [(TRACE_LOCK_NAMESPACE, key2 & 0xFFFFFFFF, 2)]
        # The single-bigint key with the very same 64 bits is a different lock:
        # Postgres keeps the two key spaces apart.
        same_bits = (TRACE_LOCK_NAMESPACE << 32) | (key2 & 0xFFFFFFFF)
        assert other.execute(
            "SELECT pg_try_advisory_xact_lock(%s::bigint)", (same_bits,)
        ).fetchone()[0] is True
