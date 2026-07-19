"""Graph-algorithm driver + algorithm-selection flag.

Two layers:

- **Entrypoint flag validation** (subprocess, no DB): an unknown
  ``INTERACTIONS_ALGORITHM`` is rejected with exit 2 before any DB work. Runs
  anywhere — it never connects.
- **Driver drain** (in-process, migrated DB via ``configured_db``): driving the
  graph loop over a loaded fixture persists production rows and advances the
  shared cursor. Needs Docker/Postgres (testcontainers), same as main's
  interactions DB tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from data_governance import db
from data_governance.processors.interactions import graph_driver
from data_governance.processors.otlp_receiver.write_span import write_span
from data_governance.processors.interactions.graph.load_fixture import _row_to_span_row

_FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "processors"
    / "interactions"
    / "graph"
    / "fixtures"
)


# --- entrypoint flag validation (no DB) -------------------------------------


def _spawn(env_extra: dict[str, str]) -> subprocess.Popen[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        **env_extra,
    }
    return subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.interactions"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_entrypoint_rejects_unknown_algorithm() -> None:
    """An unknown INTERACTIONS_ALGORITHM exits 2 before any DB connection —
    a bogus DATABASE_URL is never dialed because the flag check comes first."""
    proc = _spawn(
        {
            "DATABASE_URL": "postgres://unused:unused@127.0.0.1:1/nodb",
            "INTERACTIONS_ALGORITHM": "nonsense",
        }
    )
    rc = proc.wait(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ""
    assert rc == 2
    assert "INTERACTIONS_ALGORITHM" in stderr


# --- driver drain (migrated DB) ---------------------------------------------


def _load_into_db(name: str) -> str:
    rows = json.loads((_FIXTURES / f"{name}.json").read_text())
    trace_id = rows[0]["trace_id"]
    for row in rows:
        write_span(_row_to_span_row(row))
    return trace_id


def test_drain_from_empty_is_noop(configured_db: str) -> None:
    assert graph_driver.drain(0) == 0


def test_drain_derives_and_persists_a_trace(configured_db: str) -> None:
    """Draining spans through the graph loop derives the whole trace and writes
    production rows, advancing the shared cursor to max(seq)."""
    trace_id = _load_into_db("travel_agent_I")

    with db.transaction() as tx:
        start = graph_driver.read_cursor(tx)
    new_cursor = graph_driver.drain(start)

    with db.transaction() as tx:
        max_seq = tx.fetch_one("SELECT max(seq) FROM spans")[0]
        n_ent = tx.fetch_one("SELECT count(*) FROM entities")[0]
        n_ix = tx.fetch_one(
            "SELECT count(*) FROM interactions WHERE trace_id = %s", (trace_id,)
        )[0]

    assert new_cursor == max_seq
    # Single clarifying turn: one agent + one llm, one agent→llm interaction.
    assert n_ent == 2
    assert n_ix == 1


def test_drain_is_idempotent_on_rerun(configured_db: str) -> None:
    """Re-deriving a trace as later spans arrive converges (deterministic ids),
    it does not duplicate rows."""
    trace_id = _load_into_db("patent_agent_I")
    graph_driver.drain(0)
    with db.transaction() as tx:
        first = tx.fetch_one(
            "SELECT count(*) FROM interactions WHERE trace_id = %s", (trace_id,)
        )[0]

    # Reset the cursor and drain again: every span re-derives its whole trace.
    with db.transaction() as tx:
        tx.execute("UPDATE processor_state SET last_processed_seq = 0")
    graph_driver.drain(0)
    with db.transaction() as tx:
        second = tx.fetch_one(
            "SELECT count(*) FROM interactions WHERE trace_id = %s", (trace_id,)
        )[0]

    assert first == second
    assert first == 5  # 3 LLM calls + inferred database + file tool calls
