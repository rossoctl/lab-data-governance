"""Dev tool: load a captured test fixture into the live `spans` table, then run
the graph debug CLI over it — so a fixture-only trace becomes inspectable via the
intermediate graph tables.

WHY THIS EXISTS
---------------
The graph tests validate the extractor as a *pure function* over a list of Spans
loaded from `tests/.../fixtures/*.json` — they never touch the database (see that
package's conftest). The debug CLI (`cli.py`), by contrast, reads spans *from*
Postgres and writes the intermediate `proto_base_*` / `proto_colored_*` /
`proto_entity_*` graph tables.

So a trace that exists only as a test fixture has no spans in Postgres for the CLI
to read. This script bridges that gap by doing, in order:

  1. INSERT the fixture's span rows into the `spans` table (via the receiver's
     own `write_span`, so ON CONFLICT / finalization semantics match real
     ingestion — re-running is idempotent).
  2. Invoke the debug CLI (`cli.main`) for that trace_id, which reads those
     now-present spans, runs `extract()`, and writes the intermediate graph tables.

  !!! This WRITES TO WHATEVER `DATABASE_URL` POINTS AT. !!!
  Pointed at the deployment's Postgres, it mutates shared, persistent state
  (inserts spans + drops/recreates the intermediate proto_* graph tables). That is
  why it is DISABLED BY DEFAULT and refuses to run without an explicit opt-in.

HOW TO ENABLE
-------------
Set the env var `PI_LOAD_FIXTURE_CONFIRM=1`. Without it the script prints this
guidance and exits non-zero, writing nothing.

HOW TO RUN
----------
From inside the data-governance pod (where DATABASE_URL is already set), with
the repo's fixtures available on disk:

    PI_LOAD_FIXTURE_CONFIRM=1 \
      python -m data_governance.processors.interactions.graph.load_fixture \
      tests/processors/interactions/graph/fixtures/travel_agent_I.json

Or give just the fixture stem and let it resolve under the fixtures dir:

    PI_LOAD_FIXTURE_CONFIRM=1 \
      python -m data_governance.processors.interactions.graph.load_fixture \
      travel_agent_I

Then reload the UI and select the trace id printed at the end.

Prefer a throwaway database (a local/ephemeral Postgres, or testcontainers)
over the deployment's when you only need to eyeball a fixture.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from data_governance import db
from data_governance.processors.otlp_receiver.write_span import (
    SpanRow,
    WriteOutcome,
    write_span,
)

from . import cli

# The fixtures the P-interactions tests load from. A bare stem argument
# (e.g. "travel_agent_I") is resolved relative to here.
_FIXTURES = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "processors"
    / "interactions"
    / "graph"
    / "fixtures"
)

_CONFIRM_ENV = "PI_LOAD_FIXTURE_CONFIRM"


def _parse_dt(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _row_to_span_row(row: dict) -> SpanRow:
    """Map a captured `spans`-table fixture row back into a `SpanRow`.

    This is the inverse of the conftest's `_row_to_span`: the fixture is a
    verbatim snapshot of `spans` columns (ADR-0006, "the row is the Span"), so
    every field maps straight across. `started_at` is required by `SpanRow`;
    every other timestamp/JSON column is optional.
    """
    return SpanRow(
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        parent_id=row.get("parent_id"),
        name=row["name"],
        kind=row.get("kind"),
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row.get("ended_at")),
        error=row.get("error"),
        status_message=row.get("status_message"),
        service_name=row.get("service_name"),
        attributes=row.get("attributes") or {},
        events=row.get("events"),
        links=row.get("links"),
        scope=row.get("scope"),
        resource_attributes=row.get("resource_attributes"),
        otlp=row.get("otlp"),
    )


def _resolve_fixture(arg: str) -> Path:
    """Accept a full path, a path ending in .json, or a bare fixture stem."""
    p = Path(arg)
    if p.exists():
        return p
    candidate = _FIXTURES / (arg if arg.endswith(".json") else f"{arg}.json")
    return candidate


def _load_spans(fixture: Path) -> str:
    """Insert every span in *fixture* into the `spans` table.

    Returns the trace_id of the loaded spans. Prints a per-outcome tally so a
    re-run (all DUPLICATE/FINALIZED) is visibly distinguishable from a first
    load (all INSERTED).
    """
    rows = json.loads(fixture.read_text())
    if not rows:
        raise SystemExit(f"fixture {fixture} has no span rows")

    trace_ids = {r["trace_id"] for r in rows}
    if len(trace_ids) != 1:
        raise SystemExit(f"fixture spans span multiple traces: {sorted(trace_ids)}")
    (trace_id,) = trace_ids

    tally: dict[WriteOutcome, int] = {}
    for row in rows:
        outcome = write_span(_row_to_span_row(row))
        tally[outcome] = tally.get(outcome, 0) + 1

    summary = ", ".join(f"{o.value}={n}" for o, n in sorted(
        tally.items(), key=lambda kv: kv[0].value))
    print(f"  loaded {len(rows)} spans into `spans` ({summary})")
    return trace_id


def main() -> int:
    # --- opt-in gate: disabled by default ---------------------------------
    # This script mutates the database `DATABASE_URL` points at, so it will
    # not run unless explicitly confirmed. See the module docstring for the
    # full how-to.
    if os.environ.get(_CONFIRM_ENV) != "1":
        print(__doc__)
        print(
            f"\nREFUSING TO RUN: set {_CONFIRM_ENV}=1 to confirm you want to "
            f"write to the database at DATABASE_URL.",
            file=sys.stderr,
        )
        return 2

    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    fixture = _resolve_fixture(sys.argv[1])
    if not fixture.exists():
        print(f"ERROR: fixture not found: {fixture}", file=sys.stderr)
        return 1

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    # Step 1: load the fixture's spans into Postgres (own pool lifecycle —
    # the CLI's main() configures and closes its own pool in step 2).
    db.configure(dsn)
    try:
        print(f"loading fixture {fixture.name} into `spans`...")
        trace_id = _load_spans(fixture)
    finally:
        db.close_pool()

    # Step 2: run the graph debug CLI over the loaded trace.
    # cli.main() reads argv, so hand it the trace id we just loaded.
    print(f"\nrunning graph debug CLI for {trace_id}...\n")
    sys.argv = [sys.argv[0], trace_id]
    rc = cli.main()
    if rc == 0:
        print(f"\nloaded + processed {trace_id} — inspect its intermediate graph tables.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
