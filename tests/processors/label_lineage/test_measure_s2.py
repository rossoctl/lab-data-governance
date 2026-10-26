"""Run-15-shaped counts for slice 2, recorded (printed with ``-s``), not asserted:
on the replayed rows, each preceded by a clone of itself one hour earlier, how many
prior payloads load, how many edges cross, how many foreign-origin edges travel on,
and how many derivations the drain needs to settle."""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from data_governance import db
from data_governance.label_lineage import find
from data_governance.processors.label_lineage import driver
from tests.label_lineage.fixtures import FIXTURES
from tests.processors.label_lineage.seed import seed, seed_clone

ROWS = sorted(p.stem for p in FIXTURES.glob("R*.json"))
ON = driver.Config(find.exact, "exact", window_hours=2.0)


@pytest.mark.parametrize("row", ROWS)
def test_counts(configured_db: str, row: str, monkeypatch) -> None:
    earlier = f"{row}-earlier"
    seed_clone(configured_db, row, trace_id=earlier, delta=-dt.timedelta(hours=1), seq_offset=-100_000)
    d = seed(configured_db, row)
    calls: list[str] = []
    real = driver.derive_and_persist
    monkeypatch.setattr(driver, "derive_and_persist",
                        lambda tx, t, c: calls.append(t) or real(tx, t, c))
    driver.drain(0, ON)
    with db.transaction() as tx:
        trace = driver.load_trace(tx, d["trace_id"])
        priors = driver.load_priors(tx, trace, ON.window_hours)
    with psycopg.connect(configured_db) as conn:
        q = lambda sql, *p: conn.execute(sql, p).fetchone()[0]
        own = q("SELECT count(*) FROM payload_label_edges WHERE trace_id = %s AND origin_trace_id IS NULL", d["trace_id"])
        crossing = q("SELECT count(*) FROM payload_label_edges WHERE trace_id = %s AND from_trace_id IS NOT NULL", d["trace_id"])
        foreign = q("SELECT count(*) FROM payload_label_edges WHERE trace_id = %s AND origin_trace_id IS NOT NULL", d["trace_id"])
        legs_with_foreign_only = q(
            "SELECT count(*) FROM leg_inherited_labels m WHERE m.trace_id = %s AND m.edge_count = 0 AND EXISTS ("
            "SELECT 1 FROM payload_label_edges e WHERE e.to_interaction_id = m.interaction_id AND e.to_leg_type = m.leg_type)",
            d["trace_id"])
        dirty = q("SELECT count(*) FROM label_lineage_dirty_traces")
    print(f"\n{row}: priors {len(priors)} payloads / {sum(len(p.occurrences) for p in priors)} occurrences; "
          f"edges own {own}, crossing {crossing}, foreign-origin {foreign}; legs with foreign edges only "
          f"{legs_with_foreign_only}; derivations {len(calls)} ({calls.count(d['trace_id'])} of {row}); dirty left {dirty}")
    assert dirty == 0
