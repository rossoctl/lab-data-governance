"""Finalization tripwire (#73) for the P-interactions processor.

The processor uses ``seq`` for both the cursor and the lineage horizon
(slice #70), which is faithful only while every span has
``seq == arrival_seq``. Per ADR-0004 ``seq`` advances when a span is finalized
out of arrival order while ``arrival_seq`` is stable, so the first span with
``seq != arrival_seq`` is the signal that the deferred two-column horizon split
must be implemented. These tests drive real spans through the driver and assert
the tripwire (WARNING + ``finalization_observed_total`` counter) fires on the
mismatch and stays silent on the matched (current-state) path.
"""

from __future__ import annotations

import datetime as dt
import logging

import psycopg
import pytest

from data_governance import db
from data_governance.processors.interactions import driver, metrics


@pytest.fixture(autouse=True)
def _fresh_metrics_and_flag() -> None:
    """Isolate each test: fresh metric registry + reset the one-time warn flag.

    ``_finalization_warned`` is a module global gating the single WARNING, so it
    must be reset or a later test would never see the log line.
    """
    metrics.make_registry()
    driver._finalization_warned = False


def _insert_span(
    dsn: str,
    *,
    trace_id: str,
    span_id: str,
    parent_id: str | None,
    seq: int,
    arrival_seq: int,
) -> None:
    """Insert a minimal span with explicit ``seq`` / ``arrival_seq``.

    Unlike ``conftest._insert_spans`` (which always sets them equal), this lets a
    test force ``seq != arrival_seq`` to exercise the finalization tripwire.
    """
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO spans (trace_id, span_id, parent_id, kind, name,
                               started_at, attributes, seq, arrival_seq, observed_at)
            VALUES (%s, %s, %s, 'INTERNAL', %s, %s, '{}'::jsonb, %s, %s, now())
            """,
            (trace_id, span_id, parent_id, "n",
             dt.datetime.now(dt.timezone.utc), seq, arrival_seq),
        )
        # Keep the sequence ahead of the explicit seqs we inserted.
        conn.execute("SELECT setval('spans_seq', (SELECT max(seq) FROM spans))")
        conn.commit()


def _drain(dsn: str) -> None:
    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor)


def test_finalized_span_fires_warning_and_metric(
    configured_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A span with seq != arrival_seq increments the counter and logs once."""
    _insert_span(
        configured_db, trace_id="t1", span_id="a", parent_id=None,
        seq=5, arrival_seq=3,
    )
    with caplog.at_level(logging.WARNING, logger=driver.__name__):
        _drain(configured_db)

    assert metrics.finalization_observed_total._value.get() == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "finalization observed" in msg
    # The warning must name the deferred two-column horizon work.
    assert "arrival_seq" in msg and "horizon" in msg


def test_matched_span_is_silent(
    configured_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The current-state path (seq == arrival_seq) leaves the counter at 0 and
    logs nothing — no behaviour change when finalization is unexercised."""
    _insert_span(
        configured_db, trace_id="t1", span_id="a", parent_id=None,
        seq=1, arrival_seq=1,
    )
    with caplog.at_level(logging.WARNING, logger=driver.__name__):
        _drain(configured_db)

    assert metrics.finalization_observed_total._value.get() == 0
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_warning_is_one_time_but_metric_keeps_counting(
    configured_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A second finalized span increments the counter again but does NOT log a
    second WARNING (the log isn't flooded once finalization starts)."""
    _insert_span(
        configured_db, trace_id="t1", span_id="a", parent_id=None,
        seq=5, arrival_seq=3,
    )
    _insert_span(
        configured_db, trace_id="t1", span_id="b", parent_id="a",
        seq=6, arrival_seq=4,
    )
    with caplog.at_level(logging.WARNING, logger=driver.__name__):
        _drain(configured_db)

    assert metrics.finalization_observed_total._value.get() == 2
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
