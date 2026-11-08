"""AC-DAS-012: trace risk queries scope work to the requested trace.

Three complementary checks:

- Wall-clock latency stays under 500ms on a trace large enough that a naive
  per-interaction query pattern would blow well past that budget.
- The number of statements issued is the same for a 5-interaction trace and
  a 50-interaction trace — the load-bearing N+1 guard. A latency assertion
  alone can pass on a fast local Postgres even with an N+1 pattern; the
  query-count invariant pins a bounded number of statements (see
  ``get_trace_risk_detail``'s docstring: 6 queries, always).
- Actual risk-version scans stay within the requested live interaction
  histories despite substantial unrelated history, for both forest and
  trace-rollup queries.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

import psycopg
import pytest

from data_governance import db, retrieval
from data_governance.risk.engine.trace_compute import compute_trace_risk

_TID = "trace-perf-1"


def _uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def seed_trace(configured_db: str):
    """Seed a trace with ``n`` interactions, each with 2 legs, 3 spans, and
    2 risk record versions — so the reduction, join, and grouping the forest
    read performs are all genuinely exercised, not accidentally trivial."""

    def _seed(trace_id: str, n: int) -> None:
        with psycopg.connect(configured_db) as conn:
            conn.execute(
                "INSERT INTO trace_risk_records (trace_risk_id, trace_id, "
                "version, computed_at, trace_risk_level, interaction_count, "
                "policy_event_count) VALUES (%s, %s, %s, now(), %s, %s, %s)",
                (_uuid(), trace_id, 1, "low", n, n),
            )
            for i in range(n):
                interaction_id = f"{trace_id}-ix-{i}"
                conn.execute(
                    "INSERT INTO interactions (id, trace_id, "
                    "parent_interaction_id, caller_entity_id, "
                    "callee_entity_id, summary) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        interaction_id,
                        trace_id,
                        None,
                        "ent-caller",
                        "ent-callee",
                        "did a thing",
                    ),
                )
                for leg_type, offset in (("request", 0), ("response", 1)):
                    conn.execute(
                        "INSERT INTO interaction_legs (interaction_id, "
                        "leg_type, occurred_at) VALUES (%s, %s, %s)",
                        (
                            interaction_id,
                            leg_type,
                            dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
                            + dt.timedelta(seconds=i, milliseconds=offset),
                        ),
                    )
                for j, role in enumerate(("anchor", "info", "connector")):
                    conn.execute(
                        "INSERT INTO interaction_spans (interaction_id, "
                        "trace_id, span_id, role, leg_type) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            interaction_id,
                            trace_id,
                            f"{interaction_id}-sp-{j}",
                            role,
                            "request",
                        ),
                    )
                for version, risk_level in ((1, "low"), (2, "medium")):
                    conn.execute(
                        "INSERT INTO interaction_risk_records ("
                        "interaction_risk_id, interaction_id, trace_id, "
                        "caller_entity_id, callee_entity_id, version, "
                        "computed_at, risk_level, policy_event_count) "
                        "VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s)",
                        (
                            _uuid(),
                            interaction_id,
                            trace_id,
                            "ent-caller",
                            "ent-callee",
                            version,
                            risk_level,
                            1,
                        ),
                    )
            conn.commit()

    return _seed


def test_trace_detail_responds_within_500ms(seed_trace):
    seed_trace(_TID, 50)

    start = time.monotonic()
    detail = retrieval.get_trace_risk_detail(_TID)
    elapsed = time.monotonic() - start

    assert detail is not None
    assert len(detail.interactions) == 50
    assert elapsed < 0.5, f"took {elapsed:.3f}s"


def test_trace_detail_query_count_is_independent_of_trace_size(
    seed_trace, monkeypatch
):
    seed_trace("trace-perf-small", 5)
    seed_trace("trace-perf-large", 50)

    counts: dict[str, int] = {"n": 0}
    original_fetch_all = db.Transaction.fetch_all
    original_fetch_one = db.Transaction.fetch_one

    def counting_fetch_all(self, *args, **kwargs):
        counts["n"] += 1
        return original_fetch_all(self, *args, **kwargs)

    def counting_fetch_one(self, *args, **kwargs):
        counts["n"] += 1
        return original_fetch_one(self, *args, **kwargs)

    monkeypatch.setattr(db.Transaction, "fetch_all", counting_fetch_all)
    monkeypatch.setattr(db.Transaction, "fetch_one", counting_fetch_one)

    counts["n"] = 0
    retrieval.get_trace_risk_detail("trace-perf-small")
    small_count = counts["n"]

    counts["n"] = 0
    retrieval.get_trace_risk_detail("trace-perf-large")
    large_count = counts["n"]

    assert small_count == large_count
    assert small_count <= 7


def _risk_rows_examined(plan):
    own = 0
    if plan.get("Relation Name") == "interaction_risk_records":
        own = (plan.get("Actual Rows", 0) + plan.get("Rows Removed by Filter", 0)
               + plan.get("Rows Removed by Index Recheck", 0)) * plan.get("Actual Loops", 1)
    return own + sum(_risk_rows_examined(child) for child in plan.get("Plans", []))


@pytest.mark.parametrize("operation", ["forest", "rollup"])
def test_small_trace_does_not_scan_unrelated_risk_history(seed_trace, monkeypatch, operation):
    trace_id = "zz-perf-focus"
    seed_trace(trace_id, 5)  # ten eligible versions: five current live IDs
    with db.transaction() as tx:
        tx.execute("""
            INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary)
            SELECT '000-unrelated-' || n, 'unrelated-perf', 'noise-caller', 'noise-callee', 'synthetic'
            FROM generate_series(1, 20000) AS n
        """)
        tx.execute("""
            INSERT INTO interaction_risk_records
                (interaction_id, trace_id, caller_entity_id, callee_entity_id,
                 version, computed_at, risk_level, policy_event_count)
            SELECT i.id, i.trace_id, i.caller_entity_id, i.callee_entity_id, v, now(), 'low', 0
            FROM interactions i CROSS JOIN generate_series(1, 3) AS v
            WHERE i.trace_id = 'unrelated-perf'
        """)
        tx.execute("ANALYZE interactions")
        tx.execute("ANALYZE interaction_risk_records")

    queries = []
    original = db.Transaction.fetch_all

    def capture(self, sql, params=None):
        if "interaction_risk_records" in sql:
            queries.append((sql, params))
        return original(self, sql, params)

    monkeypatch.setattr(db.Transaction, "fetch_all", capture)
    if operation == "forest":
        detail = retrieval.get_trace_risk_detail(trace_id)
        assert len(detail.interactions) == 5
        assert all(ix.risk.version == 2 for ix in detail.interactions)
    else:
        compute_trace_risk(trace_id)
        assert retrieval.get_trace_risk(trace_id).interaction_count == 5
    assert len(queries) == 1
    sql, params = queries[0]
    with db.transaction() as tx:
        explain = tx.fetch_one("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)[0]
    # Query counts alone cannot catch a global history scan. Count actual
    # work without pinning a particular index, scan strategy, or local timing.
    scanned = _risk_rows_examined(explain[0]["Plan"])
    assert scanned <= 10, f"{operation} examined {scanned} risk versions for ten eligible versions"
