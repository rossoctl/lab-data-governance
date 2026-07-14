"""Stream-agnostic cursor-driven driver (issue #75).

The shared driver (:mod:`data_governance.processors._driver`) is the generic
drain/poll/LISTEN-wake loop extracted from the P-interactions driver so a second
processor (P-classification) reuses the proven loop rather than copying it. It is
parameterized by a :class:`~data_governance.processors._driver.StreamSpec`: the
NOTIFY channel, the ``processor_state`` cursor name, a batch-fetch function, a
per-item process function, and how to read an item's seq.

These tests exercise the shared loop through a *toy* stream that has nothing to
do with interactions — proving the module carries no interactions-specific
knowledge. The toy stream drains the migrated ``spans`` table by ``seq`` and
records progress under its own ``processor_state`` cursor row. The atomic "one
item, one transaction, cursor advances with the write" contract (ADR-0007) is
asserted by crashing mid-item and checking the cursor did not advance past — and
the crashing item's derived write rolled back with it.
"""

from __future__ import annotations

import datetime as dt
import threading
import time

import psycopg
import pytest

from data_governance import db
from data_governance.processors import _driver

# A processor name distinct from "interactions" so this toy stream's cursor row
# never collides with the real processor's.
_TOY_NAME = "toy_stream_test"
_TOY_CHANNEL = "dg_spans_inserted"


def _insert_min_span(dsn: str, *, span_id: str) -> int:
    """Insert a minimal span (DEFAULT-allocated seq); return its seq."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """
            INSERT INTO spans (trace_id, span_id, parent_id, kind, name,
                               started_at, attributes, seq, arrival_seq, observed_at)
            VALUES ('t', %s, NULL, 'INTERNAL', 'n', %s, '{}'::jsonb,
                    nextval('spans_seq'), currval('spans_seq'), now())
            RETURNING seq
            """,
            (span_id, dt.datetime.now(dt.timezone.utc)),
        ).fetchone()
        conn.commit()
    return int(row[0])


def _toy_cursor(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        r = conn.execute(
            "SELECT last_processed_seq FROM processor_state WHERE processor_name = %s",
            (_TOY_NAME,),
        ).fetchone()
    return int(r[0]) if r else 0


def _fetch_seqs(tx: db.Transaction, cursor: int, limit: int) -> list[tuple[str, int]]:
    """Toy batch-fetch: (span_id, seq) tuples past the cursor, in seq order."""
    return [
        (sid, seq)
        for sid, seq in tx.fetch_all(
            "SELECT span_id, seq FROM spans WHERE seq > %s ORDER BY seq ASC LIMIT %s",
            (cursor, limit),
        )
    ]


def _make_spec(process=None) -> _driver.StreamSpec:
    def _record(tx: db.Transaction, item: tuple[str, int]) -> None:
        # Trivial derived write: mark the span processed by setting its name.
        tx.execute("UPDATE spans SET name = 'seen' WHERE span_id = %s", (item[0],))

    return _driver.StreamSpec(
        notify_channel=_TOY_CHANNEL,
        processor_name=_TOY_NAME,
        fetch_batch=_fetch_seqs,
        process_item=process or _record,
        item_seq=lambda item: item[1],
    )


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    """No items past the cursor → cursor stays put."""
    spec = _make_spec()
    with db.transaction() as tx:
        cursor = _driver.read_cursor(tx, _TOY_NAME)
    assert _driver.drain(spec, cursor) == 0


def test_drain_advances_cursor_over_items(configured_db: str) -> None:
    """A drain processes every item past the cursor and advances to max(seq)."""
    seqs = [_insert_min_span(configured_db, span_id=f"s{i}") for i in range(3)]
    spec = _make_spec()
    with db.transaction() as tx:
        cursor = _driver.read_cursor(tx, _TOY_NAME)
    new_cursor = _driver.drain(spec, cursor)
    assert new_cursor == max(seqs)
    assert _toy_cursor(configured_db) == max(seqs)


def test_drain_is_incremental_across_two_batches(configured_db: str) -> None:
    """A second drain only processes items past the first drain's cursor."""
    a = _insert_min_span(configured_db, span_id="a")
    spec = _make_spec()
    with db.transaction() as tx:
        c0 = _driver.read_cursor(tx, _TOY_NAME)
    c1 = _driver.drain(spec, c0)
    assert c1 == a

    b = _insert_min_span(configured_db, span_id="b")
    c2 = _driver.drain(spec, c1)
    assert c2 == b and c2 > c1


def test_cursor_advance_is_atomic_with_the_write(configured_db: str) -> None:
    """ADR-0007: the cursor advance commits in the SAME transaction as the
    per-item write. A crash mid-item rolls back BOTH — the cursor does not
    advance past the crashing item, and its derived write is not persisted.
    """
    good = _insert_min_span(configured_db, span_id="good")
    _insert_min_span(configured_db, span_id="boom")

    def _process(tx: db.Transaction, item: tuple[str, int]) -> None:
        tx.execute("UPDATE spans SET name = 'seen' WHERE span_id = %s", (item[0],))
        if item[0] == "boom":
            raise RuntimeError("crash mid-item")

    spec = _make_spec(process=_process)
    with db.transaction() as tx:
        cursor = _driver.read_cursor(tx, _TOY_NAME)
    with pytest.raises(RuntimeError, match="crash mid-item"):
        _driver.drain(spec, cursor)

    # "good" committed (its write + cursor advance); "boom" rolled back entirely.
    assert _toy_cursor(configured_db) == good
    with psycopg.connect(configured_db) as conn:
        boom_name = conn.execute(
            "SELECT name FROM spans WHERE span_id = 'boom'"
        ).fetchone()[0]
    assert boom_name == "n", "boom's derived write must roll back with the cursor"


def test_run_falls_back_to_poll_when_listen_unavailable(
    configured_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the LISTEN connection can't be opened, run() falls back to poll-only
    and still drains — correctness never depends on NOTIFY (issue #71)."""
    def _boom(channel, dsn):  # noqa: ANN001 — test stub
        raise db.ConnectionTimeout("listen refused (injected)")

    monkeypatch.setattr(db, "listen", _boom)
    spec = _make_spec()
    object.__setattr__(spec, "poll_seconds", 0.2)

    seq = _insert_min_span(configured_db, span_id="s0")
    stop = threading.Event()
    t = threading.Thread(
        target=_driver.run, args=(spec, stop, configured_db), daemon=True
    )
    t.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _toy_cursor(configured_db) != seq:
            time.sleep(0.05)
        assert _toy_cursor(configured_db) == seq, "poll fallback did not drain"
    finally:
        stop.set()
        t.join(timeout=10)
        assert not t.is_alive()
