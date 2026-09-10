"""Settling: a live trace is "done" only when every consumer has drained
its stream to the head AND nothing new has arrived for the trace in a
while. There is no terminal state to wait for — a request-only interaction
never becomes complete (FR-DAS-022 forbids a completeness flag), and the
NOTIFY channels are latency taps with no correctness weight — so the only
honest condition is the cursors.

Every stream the pipeline cursors on, in pipeline order. ``gate`` names a
capability the deployed branch may lack (``Caps`` in conftest); a gated
stream whose capability is absent is not waited on and its absence is
recorded, never assumed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# (name, head SQL, processor_state.processor_name, poll backstop seconds, gate)
STREAMS: list[tuple[str, str, str, int, str | None]] = [
    ("interactions", "SELECT COALESCE(max(seq), 0) FROM spans", "interactions", 5, None),
    ("classification", "SELECT COALESCE(max(seq), 0) FROM interaction_payloads",
     "classification", 5, None),
    ("leg_ready", "SELECT COALESCE(max(seq), 0) FROM interaction_legs", "leg_ready", 5, None),
    ("risk_trace_trigger", "SELECT COALESCE(max(seq), 0) FROM interaction_risk_records",
     "risk_trace_trigger", 10, None),
    ("risk_alerts", "SELECT COALESCE(max(seq), 0) FROM trace_risk_records",
     "risk_alerts", 10, "alerts"),
]

_CURSOR_SQL = (
    "SELECT COALESCE((SELECT last_processed_seq FROM processor_state "
    "WHERE processor_name = %s), 0)"
)
_TRACE_SQL = "SELECT count(*), COALESCE(max(arrival_seq), 0) FROM spans WHERE trace_id = %s"
_HELD_LEGS_SQL = """
    SELECT l.seq, l.interaction_id, l.leg_type::text, l.payload_hash
    FROM interaction_legs l
    LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
    WHERE l.seq > %s AND l.payload_hash IS NOT NULL AND pc.content_hash IS NULL
    ORDER BY l.seq LIMIT 10"""


@dataclass
class Settle:
    trace_id: str
    elapsed: float
    spans: int
    polls: int
    pending_history: list[dict[str, int]] = field(default_factory=list)
    streams_waited: list[str] = field(default_factory=list)
    streams_absent: list[str] = field(default_factory=list)


def pending(dg, caps) -> dict[str, int]:
    """head − cursor per stream the deployed branch runs."""
    out: dict[str, int] = {}
    for name, head_sql, proc, _poll, gate in STREAMS:
        if gate and not getattr(caps, gate):
            continue
        head = dg.one(head_sql)[0]
        cursor = dg.one(_CURSOR_SQL, (proc,))[0]
        out[name] = int(head) - int(cursor)
    return out


def held_legs(dg, caps) -> list[tuple]:
    """Legs past the leg-ready cursor that cannot be delivered because
    their payload is not classified yet — the usual reason a drain does
    not reach the head."""
    cursor = dg.one(_CURSOR_SQL, ("leg_ready",))[0]
    return dg.q(_HELD_LEGS_SQL, (cursor,))


def trace_state(dg, trace_id: str) -> tuple[int, int]:
    n, arrival = dg.one(_TRACE_SQL, (trace_id,))
    return int(n), int(arrival)


def wait_drained(dg, trace_id: str, caps, *, timeout: float, quiet_seconds: float,
                 interval: float = 2.0) -> Settle:
    """Block until (a) the trace's span set has not changed for
    ``quiet_seconds`` (chosen ≥ the longest poll backstop, 10 s, so a
    NOTIFY-less delivery still lands inside the window) and (b) every
    deployed stream is at its head. Fails with the per-stream backlog, the
    legs held behind an unclassified payload, and the health body when the
    branch serves one."""
    t0 = time.monotonic()
    stable_since = t0
    last = trace_state(dg, trace_id)
    history: list[dict[str, int]] = []
    polls = 0
    while True:
        polls += 1
        now = trace_state(dg, trace_id)
        if now != last:
            last, stable_since = now, time.monotonic()
        pend = pending(dg, caps)
        history.append(pend)
        quiet = time.monotonic() - stable_since >= quiet_seconds
        if quiet and all(v == 0 for v in pend.values()):
            waited = [n for n, *_r, g in STREAMS if not g or getattr(caps, g)]
            absent = [n for n, *_r, g in STREAMS if g and not getattr(caps, g)]
            return Settle(trace_id=trace_id, elapsed=time.monotonic() - t0, spans=now[0],
                          polls=polls, pending_history=history[-20:],
                          streams_waited=waited, streams_absent=absent)
        if time.monotonic() - t0 > timeout:
            held = held_legs(dg, caps)
            raise AssertionError(
                f"trace {trace_id} did not settle in {timeout:.0f}s: pending={pend} "
                f"quiet={quiet} spans={now[0]} held_legs={held}"
            )
        time.sleep(interval)


def snapshot_versions(dg, trace_id: str) -> dict:
    """What a quiet re-check compares: per-interaction max version, the
    trace's max version, and the span count."""
    rows = dg.q(
        "SELECT interaction_id, max(version) FROM interaction_risk_records "
        "WHERE trace_id = %s GROUP BY interaction_id", (trace_id,))
    (tv,) = dg.one(
        "SELECT COALESCE(max(version), 0) FROM trace_risk_records WHERE trace_id = %s",
        (trace_id,))
    n, _ = trace_state(dg, trace_id)
    return {"records": {r[0]: r[1] for r in rows}, "trace_version": tv, "spans": n}


def quiet_recheck(dg, trace_id: str, *, quiet_seconds: float) -> dict:
    """Sleep one quiet window and prove nothing moved: no new record
    version, no new trace version, no new span. Returns the snapshot."""
    before = snapshot_versions(dg, trace_id)
    time.sleep(quiet_seconds)
    after = snapshot_versions(dg, trace_id)
    assert after == before, f"trace {trace_id} drifted after settling: {before} -> {after}"
    return after
