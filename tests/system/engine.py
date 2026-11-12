"""Run one case through the real system and read the rows back.

receiver (OTLP gRPC) -> interactions drain -> data-lineage drain -> classification
drain (replayed model output through the production seam) -> label-lineage drain
-> leg-ready drain with the risk observer against a real OPA -> rows.

A drain may stop early by design (leg-ready holds its cursor while OPA is down) and
returns where it stopped: a run where a drain stopped short of its stream's end or
left work in its queue is red (:func:`unfinished`), whatever the rows say.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections import defaultdict
from typing import Any

import psycopg

from data_governance import db
from data_governance.label_lineage import find
from data_governance.processors.classification import driver as classification
from data_governance.processors.data_lineage import driver as data_lineage
from data_governance.processors.interactions import sidecar_driver as interactions
from data_governance.processors.label_lineage import driver as label_lineage
from data_governance.processors.leg_ready import driver as leg_ready
from data_governance.risk import config as risk_config
from data_governance.risk.engine.observer import make_risk_observer
from tools.load_trace import rows_to_request

from . import classify
from .cases import Case
from .render import Rendered, render

_KEEP = {"alembic_version"}


def reset(dsn: str) -> None:
    """Empty every table of the migrated schema (not the schema itself)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
        tables = ", ".join(f'"{n}"' for n in names if n not in _KEEP)
        conn.execute(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")


@dataclasses.dataclass
class LegRows:
    leg_index: int
    interaction_id: str | None
    leg_type: str
    payload_hash: str | None
    classification: dict[str, Any] | None      # sensitivity, tags, findings [(tag, text)]
    inherited: list[dict[str, Any]]            # edges INTO this leg: unit, value, from_leg
    risk: dict[str, Any] | None                # the risk record that evidences this leg (sink only)


@dataclasses.dataclass
class Observation:
    mode: str                                  # "on" | "off"
    legs: dict[int, LegRows]
    edges: set[tuple[str, int, int]]           # (unit, from_leg, to_leg) resolved to line legs
    raw_edges: list[dict[str, Any]]
    verdict: str | None                        # at the sink: enforcement_type as the record writes it
    timings: dict[str, float]
    observer_calls: int                        # risk-observer invocations (one per evidence state)
    problems: list[str] = dataclasses.field(default_factory=list)   # rows the judge cannot read: always red


def _send(rows: list[dict[str, Any]], grpc_endpoint: str) -> None:
    import grpc
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc

    request = rows_to_request(rows)
    with grpc.insecure_channel(grpc_endpoint) as channel:
        trace_service_pb2_grpc.TraceServiceStub(channel).Export(request)


def _wait_rows(dsn: str, n: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    with psycopg.connect(dsn) as conn:
        while time.monotonic() < deadline:
            (c,) = conn.execute("SELECT count(*) FROM spans").fetchone()
            if c >= n:
                return
            time.sleep(0.02)
    raise TimeoutError(f"receiver stored fewer than {n} spans")


def verdict_at(records: list[dict[str, Any]], role: str) -> tuple[dict[str, Any] | None, str | None]:
    """The risk record of the flagged exchange that evidences the flagged leg (*role* in its ``legs``), and a
    problem when there is none or several that disagree. The verdict is its ``enforcement_type``, as written.
    The harness sends every span before the processors run, so the record evidences the exchange's response
    too; the renderer refuses a flagged exchange whose response carries a unit, and the judge one whose
    response the model labelled."""
    mine = [r for r in records if role in r["legs"]]
    if not mine:
        return None, f"no risk record evidences the flagged {role} leg"
    if len({r["enforcement_type"] for r in mine}) > 1:
        return None, ("risk records disagree at the flagged leg: "
                      + ", ".join(f"v{r['version']} {r['enforcement_type']}" for r in mine))
    return mine[-1], None


def flagged_response_problems(rendered: list[Rendered], legs: dict[int, "LegRows"], sink: int) -> list[str]:
    """The flagged exchange's response must reach the verdict with nothing labelled in it."""
    req = next(r.request_span_id for r in rendered if r.leg.index == sink)
    resp = next(r.leg.index for r in rendered if r.request_span_id == req and r.role == "response")
    c = legs[resp].classification if resp in legs else None
    if c is None:
        return [f"the flagged exchange's response (leg {resp}) has no classification row"]
    if c["findings"]:
        return [f"the flagged exchange's response (leg {resp}) was labelled {sorted(c['findings'])}; "
                f"the exchange's verdict would rest on it"]
    return []


def map_legs(rendered: list[Rendered], ix_of: dict[str, str], hash_of: dict[tuple[str, str], str],
             payloads: set[str]):
    """line leg -> (interaction, leg_type) and back, one to one. Every leg the line put on the wire must have been
    derived (its interaction, its leg row and its stored payload), every derived leg row must sit on one line leg,
    and no two line legs may land on one row; anything else is a problem."""
    key_of_leg: dict[int, tuple[str, str]] = {}
    leg_of_key: dict[tuple[str, str], int] = {}
    problems: list[str] = []
    claimed = {(ix_of[r.request_span_id], r.role) for r in rendered if r.request_span_id in ix_of}
    for r in rendered:
        iid = ix_of.get(r.request_span_id)
        missing = ("interaction" if iid is None else "leg row" if (iid, r.role) not in hash_of
                   else "payload" if hash_of[(iid, r.role)] not in payloads else None)
        if missing:
            problems.append(f"leg {r.leg.index} ({r.leg.label()}) was not derived: no {missing}")
            continue
        if (iid, r.role) in leg_of_key:
            problems.append(f"legs {leg_of_key[(iid, r.role)]} and {r.leg.index} land on one derived {r.role} row")
            continue
        key_of_leg[r.leg.index] = (iid, r.role)
        leg_of_key[(iid, r.role)] = r.leg.index
    problems += [f"a derived {role} row of interaction {iid} sits on no line leg"
                 for iid, role in sorted(set(hash_of) - claimed)]
    return key_of_leg, leg_of_key, problems


def resolve_edges(case: Case, rows, leg_of_key: dict[tuple[str, str], int]):
    """Edges as (unit, from_leg, to_leg). A unit is found by its bytes (``assign_values`` refuses twins); an
    edge whose value is no unit's is kept as ``?value`` (an extra, so red). An edge with an end on no line leg,
    carrying a label that is not one of its unit's types, or whose evidence is not a whole match of the value's
    own bytes (the suite runs the exact finder), is a problem."""
    value_unit = {v: u for u, v in case.values.items()}
    resolved: set[tuple[str, int, int]] = set()
    raw: list[dict[str, Any]] = []
    inherited: dict[int, list[dict[str, Any]]] = defaultdict(list)
    problems: list[str] = []
    for fi, flt, ti, tlt, etype, value, matched, kind in rows:
        f = leg_of_key.get((fi, flt))
        to = leg_of_key.get((ti, tlt))
        unit = value_unit.get(value, f"?{value}")
        raw.append({"from": f, "to": to, "unit": unit, "label": etype, "value": value, "kind": kind})
        if f is None or to is None:
            problems.append(f"edge {unit} {f}>{to} ({etype}) has an end on no line leg")
            continue
        if unit in case.accepts and etype not in case.accepts[unit]:
            problems.append(f"edge {unit} {f}>{to} carries {etype}, not one of {sorted(case.accepts[unit])}")
        if kind != "whole" or matched != value:
            problems.append(f"edge {unit} {f}>{to} matched {matched!r} as {kind!r}, not the whole value")
        resolved.add((unit, f, to))
        inherited[to].append({"unit": unit, "label": etype, "value": value, "from": f})
    return resolved, raw, inherited, problems


# Each drain the run calls and the table whose ``seq`` its cursor walks; the work queues the drains own. With
# inherited labels off, leg-ready keeps a label-only recompute request for a later `on` run: not work left undone.
STREAMS = {"interactions": "spans", "data_lineage": "interaction_legs", "classification": "interaction_payloads",
           "label_lineage": "interaction_legs", "leg_ready": "interaction_legs"}
QUEUES = {"data_lineage": "data_lineage_dirty_traces", "label_lineage": "label_lineage_dirty_traces",
          "leg_ready": "risk_recompute_requests"}
_RECOMPUTE_WORK = {"on": "base_recompute OR labels_pending", "off": "base_recompute"}


def unfinished(cursors: dict[str, int], heads: dict[str, int], queued: dict[str, int]) -> list[str]:
    """What a run left undone: a drain whose returned cursor is not its stream's last seq, a queue with work in it."""
    return ([f"incomplete run: {s} stopped at cursor {cursors[s]} of {heads[s]}" for s in STREAMS
             if cursors[s] != heads[s]]
            + [f"incomplete run: {s} left {queued[s]} row(s) in {QUEUES[s]}" for s in QUEUES if queued[s]])


def backlog(dsn: str, mode: str) -> tuple[dict[str, int], dict[str, int]]:
    """Each stream's last seq and each queue's work, read after the last drain."""
    where = {"leg_ready": _RECOMPUTE_WORK[mode]}
    with psycopg.connect(dsn) as conn:
        heads = {s: conn.execute(f"SELECT coalesce(max(seq), 0) FROM {t}").fetchone()[0] for s, t in STREAMS.items()}
        queued = {s: conn.execute(f"SELECT count(*) FROM {t} WHERE {where.get(s, 'true')}").fetchone()[0]
                  for s, t in QUEUES.items()}
    return heads, queued


def run_case(case: Case, *, dsn: str, grpc_endpoint: str, opa_client, mode: str) -> Observation:
    timings: dict[str, float] = {}
    t = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal t
        now = time.perf_counter()
        timings[name] = round(now - t, 4)
        t = now

    reset(dsn)
    db.close_pool()
    db.configure(dsn)
    lap("reset")

    renderer = render(case.id, case.units, case.line)
    rows = renderer.rows
    _send(rows, grpc_endpoint)
    _wait_rows(dsn, len(rows))
    lap("receiver")

    cursors = {"interactions": interactions.drain(0)}
    lap("interactions")
    cursors["data_lineage"] = data_lineage.drain(0)
    lap("data_lineage")
    detector = classify.load(case.dir)
    classify.check_authored(case, renderer, detector.authored)
    cursors["classification"] = classification.drain(0, detector=detector, model_version=detector.model_version)
    lap("classification")
    cursors["label_lineage"] = label_lineage.drain(0, label_lineage.Config(find.exact, "exact"))
    lap("label_lineage")

    saved = risk_config.inherited_labels_mode
    saved_hold = list(leg_ready._label_hold)
    risk_config.inherited_labels_mode = lambda: mode  # the production switch, set for this run
    leg_ready._label_hold[:] = [None, 0.0]
    calls = {"n": 0}
    inner = make_risk_observer(opa_client)

    def counting(*a, **k):
        calls["n"] += 1
        return inner(*a, **k)

    try:
        cursors["leg_ready"] = leg_ready.drain(0, observer=counting)
    finally:
        risk_config.inherited_labels_mode = saved
        leg_ready._label_hold[:] = saved_hold
    lap("leg_ready+risk")

    obs = _read(case, renderer.rendered, dsn, mode)
    obs.problems[:0] = unfinished(cursors, *backlog(dsn, mode))
    obs.timings = timings
    obs.observer_calls = calls["n"]
    lap("read")
    obs.timings["total"] = round(sum(timings.values()), 4)
    return obs


def _read(case: Case, rendered: list[Rendered], dsn: str, mode: str) -> Observation:
    with psycopg.connect(dsn) as conn:
        # request span id -> interaction id
        ix_of = dict(conn.execute(
            "SELECT span_id, interaction_id FROM interaction_spans WHERE role = 'anchor'").fetchall())
        legs_sql = conn.execute(
            "SELECT interaction_id, leg_type::text, payload_hash FROM interaction_legs").fetchall()
        hash_of = {(i, lt): h for i, lt, h in legs_sql}
        cls = {r[0]: {"sensitivity": r[1], "tags": list(r[2] or []), "bundle": r[3], "findings": r[4]}
               for r in conn.execute(
                   "SELECT content_hash, sensitivity_level, regulatory_tags, contains_identity_bundle, findings "
                   "FROM payload_classifications").fetchall()}
        payload_text = {}
        for h, content in conn.execute("SELECT content_hash, content FROM interaction_payloads").fetchall():
            from data_governance.processors.classification import projection
            payload_text[h] = projection.project(content)
        edges = conn.execute(
            "SELECT from_interaction_id, from_leg_type::text, to_interaction_id, to_leg_type::text, "
            "entity_type, unit_value, matched_text, match_kind FROM payload_label_edges").fetchall()
        risk_rows = conn.execute(
            "SELECT interaction_id, version, risk_level, enforcement_type, triggered_rule_ids, legs_evidenced, "
            "classification_summary FROM interaction_risk_records ORDER BY interaction_id, version").fetchall()

    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for iid, version, level, enf, rules, legs_ev, summary in risk_rows:
        records[iid].append({"version": version, "risk_level": level, "enforcement_type": enf,
                             "rules": list(rules or []), "legs": list(legs_ev or []),
                             "summary": summary if isinstance(summary, dict) else json.loads(summary or "{}")})

    key_of_leg, leg_of_key, problems = map_legs(rendered, ix_of, hash_of, set(payload_text))
    resolved, raw, inherited, edge_problems = resolve_edges(case, edges, leg_of_key)
    problems += edge_problems
    sink = case.line.sink
    sink_record, verdict = None, None
    if sink:
        key = key_of_leg.get(sink.index)
        if key is not None:
            sink_record, problem = verdict_at(records.get(key[0], []), key[1])
            if problem:
                problems.append(problem)
            verdict = sink_record["enforcement_type"] if sink_record else None

    legs: dict[int, LegRows] = {}
    for r in rendered:
        key = key_of_leg.get(r.leg.index)
        h = hash_of.get(key) if key else None
        c = cls.get(h) if h else None
        if c is not None:
            text = payload_text[h]
            c = {**c, "findings": [(f["entity_type"], text[f["start"]:f["end"]]) for f in c["findings"]]}
        legs[r.leg.index] = LegRows(
            leg_index=r.leg.index, interaction_id=key[0] if key else None, leg_type=r.role, payload_hash=h,
            classification=c, inherited=inherited.get(r.leg.index, []),
            risk=sink_record if sink and r.leg.index == sink.index else None,
        )

    if sink:
        problems += flagged_response_problems(rendered, legs, sink.index)
    return Observation(mode=mode, legs=legs, edges=resolved, raw_edges=raw, verdict=verdict, timings={},
                       observer_calls=0, problems=problems)

