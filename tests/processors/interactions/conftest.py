"""Fixtures for the P-interactions processor tests.

The processor reads the ``spans`` table and writes the derived graph, so tests
run against a real migrated Postgres (testcontainers, via the shared
``migrated_dsn`` fixture). The captured 281-span trace ``2b7b88e0`` is loaded
into ``spans`` in ``seq`` order — the order a streaming consumer would see it —
so the verified ``--scramble`` output transfers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from data_governance import db

# The captured fixture lives in the test tree (relocated out of the prototype
# in #72) so retiring the proto module cannot break this gate.
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "trace-2b7b88e0.json"

TRACE_2B7B88E0 = "2b7b88e0f0df1fc9936b9a31f26aebc3"

# The verified output the prototype produces for this trace (memory
# proto-b-fails-on-new-281-trace, post-fix): the gate the processor must match.
# These mirror the captured ``baseline-scramble-2b7b88e0.txt`` in ``fixtures/``,
# which is the CORRECT (order-independent) baseline.
EXPECTED_INTERACTIONS = 31
EXPECTED_ENTITIES = 15
EXPECTED_PAYLOADS = 50

# interaction_spans role split (= 201 rows) from the scramble baseline — pinned
# so a future classifier refactor cannot silently re-introduce arrival-order
# span-attachment divergence in the graph. (entity_spans total is 62 rows; its
# role split is provenance, not graph, and is not pinned — see test_scramble.)
EXPECTED_INTERACTION_SPAN_ROLES = {"anchor": 35, "connector": 66, "info": 100}


# ``configured_db`` (a migrated DB with the pool pointed at it) is provided by
# the parent ``tests/processors/conftest.py`` so every processor's tests share
# one definition (issue #75).


def _admin_url_to_db(admin_dsn: str, dbname: str) -> str:
    head, _, _ = admin_dsn.rpartition("/")
    return f"{head}/{dbname}"


@pytest.fixture()
def make_migrated_db(_admin_dsn: str) -> Iterator[Callable[[], str]]:
    """Factory minting fresh, independently-migrated databases on demand.

    The scramble gate needs TWO independent migrated DBs within a single test
    (in-order vs scrambled arrival), but ``migrated_dsn``/``pg_dsn`` are
    function-scoped — pytest caches one per test, so depending on two
    span-loader fixtures would share a single DB. This factory creates a fresh
    ``t_<uuid>`` database (reusing the session container) per call and drops
    them all when the test finishes.
    """
    created: list[str] = []

    def _make() -> str:
        dbname = f"t_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(_admin_dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{dbname}"')
        created.append(dbname)
        dsn = _admin_url_to_db(_admin_dsn, dbname)
        result = subprocess.run(
            [sys.executable, "-m", "data_governance.db.migrate"],
            env={"DATABASE_URL": dsn, "PATH": "/usr/bin:/bin"},
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"migrate exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        return dsn

    try:
        yield _make
    finally:
        for dbname in created:
            with psycopg.connect(_admin_dsn, autocommit=True) as conn:
                conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (dbname,),
                )
                conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


def _load_fixture_spans() -> list[dict[str, Any]]:
    spans = json.loads(_FIXTURE.read_text())
    # Sort by the fixture's own seq — the order a seq-cursoring consumer drains.
    return sorted(spans, key=lambda s: s["seq"])


def _resequence(spans: list[dict[str, Any]], order: list[int]) -> list[dict[str, Any]]:
    """Return copies of *spans* with fresh monotonic ``seq``/``arrival_seq``
    assigned in the position given by *order* (a permutation of the indices
    into the seq-sorted list).

    The driver drains by ``seq`` (``WHERE seq > cursor ORDER BY seq``), so the
    only way to change the *arrival* order the processor sees is to re-assign
    seqs — preserving each span's ``parent_id`` (a span_id, not a seq) so the
    trace structure is untouched while children can now arrive before parents.
    Simply shuffling INSERT order without re-sequencing is a no-op: the seq
    horizon would replay the same order regardless.
    """
    resequenced: list[dict[str, Any]] = []
    for new_seq, idx in enumerate(order, start=1):
        s = dict(spans[idx])
        s["seq"] = new_seq
        s["arrival_seq"] = new_seq
        resequenced.append(s)
    return resequenced


def _insert_spans(dsn: str, spans: list[dict[str, Any]]) -> None:
    """Insert span rows with each span's ``seq``/``arrival_seq`` as given.

    Explicit seq values fix the arrival order and the ``seq == arrival_seq``
    invariant the horizon relies on. Rows are inserted ordered by ``seq`` so
    INSERT order matches drain order (immaterial to correctness — the driver
    re-reads by seq — but keeps the table readable).
    """
    def _j(v: Any) -> str | None:
        return json.dumps(v) if v is not None else None

    with psycopg.connect(dsn) as conn:
        for s in sorted(spans, key=lambda r: r["seq"]):
            conn.execute(
                """
                INSERT INTO spans (
                    trace_id, span_id, parent_id, kind, name, service_name,
                    started_at, ended_at, error, status_message,
                    attributes, events, links, otlp, scope, resource_attributes,
                    seq, arrival_seq, observed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s
                )
                """,
                (
                    s["trace_id"], s["span_id"], s.get("parent_id"), s.get("kind"),
                    s["name"], s.get("service_name"),
                    s["started_at"], s.get("ended_at"), s.get("error"),
                    s.get("status_message"),
                    _j(s.get("attributes") or {}), _j(s.get("events")),
                    _j(s.get("links")), _j(s.get("otlp")), _j(s.get("scope")),
                    _j(s.get("resource_attributes")),
                    s["seq"], s["arrival_seq"], s["observed_at"],
                ),
            )
        # Keep spans_seq ahead of the explicit values we just inserted so any
        # later DEFAULT-driven insert does not collide.
        conn.execute(
            "SELECT setval('spans_seq', (SELECT max(seq) FROM spans))"
        )
        conn.commit()


@pytest.fixture()
def loaded_trace(configured_db: str) -> str:
    """Load the 281-span fixture into ``spans`` in seq order. Returns the DSN."""
    _insert_spans(configured_db, _load_fixture_spans())
    return configured_db


@pytest.fixture()
def scrambled_trace(configured_db: str) -> str:
    """Load the 281-span fixture with seqs RE-ASSIGNED in reversed-seq order.

    This is the production analogue of the prototype's
    ``extract(scramble_for_late_parent=True)`` (which processed
    ``reversed(sorted_by_seq)``): every parent now has a higher seq than its
    children, so the driver drains children first and the late-parent
    re-derive path fires. The derived graph must come out identical to
    ``loaded_trace`` — that equality is the ``--scramble`` gate (#72).
    """
    ordered = _load_fixture_spans()
    reversed_order = list(reversed(range(len(ordered))))
    _insert_spans(configured_db, _resequence(ordered, reversed_order))
    return configured_db


# --- shared drain + snapshot helpers ----------------------------------------
# Used by both test_processor.py (verified-counts / cursor-reset) and
# test_scramble.py (arrival-order gate).

def drain_all(dsn: str) -> None:
    """Drain the whole spans table from cursor 0."""
    # Import here so importing this conftest never pulls in the driver before
    # the DB pool is configured.
    from data_governance.processors.interactions import driver

    with db.transaction() as tx:
        cursor = driver.read_cursor(tx)
    driver.drain(cursor)


def load_drain_snapshot(dsn: str, *, scramble: bool) -> dict[str, list]:
    """Point the pool at *dsn*, load the 281-span fixture (in-order or
    re-sequenced for scrambled arrival), drain it, and return the derived
    snapshot. Closes the pool on the way out so the caller can reuse it for
    another DB.
    """
    ordered = _load_fixture_spans()
    if scramble:
        order = list(reversed(range(len(ordered))))
        spans = _resequence(ordered, order)
    else:
        spans = ordered
    db.close_pool()
    db.configure(dsn)
    try:
        _insert_spans(dsn, spans)
        drain_all(dsn)
        return snapshot(dsn)
    finally:
        db.close_pool()


def snapshot(dsn: str) -> dict[str, list]:
    """Order-stable snapshot of every derived table for equality comparison.

    Ordered by the table's natural/derived keys (not by ``seq``) so the dump is
    invariant to span arrival order — two drains that derived the same graph
    compare equal regardless of the order spans arrived in.
    """
    with psycopg.connect(dsn) as conn:
        def rows(sql: str) -> list:
            return conn.execute(sql).fetchall()

        return {
            # seq/original_seq are INTENTIONALLY omitted: they are frozen at
            # entity creation to the first-touching span, which is arrival-order
            # dependent (write-only passenger fields, read by no classifier
            # decision). Making them arrival-invariant is a deferred ADR-level
            # data-model change, not part of the derived-graph equivalence this
            # gate enforces.
            "entities": rows(
                "SELECT id, kind, natural_key, display_name, project_name, "
                "detected_from FROM entities ORDER BY natural_key"
            ),
            # The parent interactions row is identity only (ADR-0025); its
            # columns are all arrival-invariant.
            "interactions": rows(
                "SELECT id, trace_id, parent_interaction_id, caller_entity_id, "
                "callee_entity_id, summary FROM interactions ORDER BY id"
            ),
            # The leg-dependent fields moved to interaction_legs (ADR-0025).
            # occurred_at/error ARE compared: they must be arrival-invariant
            # (occurred_at is a min/max fold over the leg's territory spans, and
            # the aggregate recompute folds into the persisted value so a
            # lineage-scoped re-aggregate cannot narrow the window). seq is
            # omitted for the same reason entity seqs are: it is a DB-owned
            # per-leg nextval, arrival-order dependent and read by no consumer
            # decision (issue #133 dropped the once-parallel leg original_seq).
            "interaction_legs": rows(
                "SELECT interaction_id, leg_type::text, occurred_at, "
                "payload_hash, error FROM interaction_legs "
                "ORDER BY interaction_id, leg_type"
            ),
            "entity_spans": rows(
                "SELECT entity_id, trace_id, span_id, role FROM entity_spans "
                "ORDER BY trace_id, span_id, entity_id, role"
            ),
            "interaction_spans": rows(
                "SELECT interaction_id, trace_id, span_id, role, leg_type::text "
                "FROM interaction_spans ORDER BY trace_id, span_id"
            ),
            "payloads": rows(
                "SELECT content_hash, content_kind, byte_size FROM interaction_payloads "
                "ORDER BY content_hash"
            ),
        }
