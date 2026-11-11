"""Run one case through the real system and read the rows back.

receiver (OTLP gRPC) -> interactions drain -> data-lineage drain -> classification
drain (replayed model output through the production seam) -> label-lineage drain
-> leg-ready drain with the risk observer against a real OPA -> rows.
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
WINDOW_ENV = "LABEL_CROSS_TRACE_WINDOW_HOURS"


def window_hours() -> float:
    import os
    return float(os.environ.get(WINDOW_ENV, "0.25"))


def _label_config():
    import os
    fields = {f.name for f in dataclasses.fields(label_lineage.Config)}
    kw = {}
    if "window_hours" in fields:
        kw["window_hours"] = window_hours()
    if "seed_rule" in fields:
        kw["seed_rule"] = os.environ.get("LABEL_SEED_RULE", "off") or "off"
    return label_lineage.Config(find.exact, "exact", **kw)


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
    neighbourhood: dict[int, dict[str, str]]   # leg -> unit -> "k of n"
    opa_calls: int
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
    """The risk record that evidences the flagged leg (*role* in its ``legs``), and a problem when there is
    none or several that disagree. The verdict is its ``enforcement_type``, as written."""
    mine = [r for r in records if role in r["legs"]]
    if not mine:
        return None, f"no risk record evidences the flagged {role} leg"
    if len({r["enforcement_type"] for r in mine}) > 1:
        return None, ("risk records disagree at the flagged leg: "
                      + ", ".join(f"v{r['version']} {r['enforcement_type']}" for r in mine))
    return mine[-1], None


def map_legs(rendered: list[Rendered], ix_of: dict[str, str]):
    """line leg -> (interaction, leg_type) and back. Every leg the line put on the wire must have been derived;
    one that was not is a problem."""
    key_of_leg: dict[int, tuple[str, str]] = {}
    leg_of_key: dict[tuple[str, str], int] = {}
    problems: list[str] = []
    for r in rendered:
        iid = ix_of.get(r.request_span_id)
        if iid is None:
            problems.append(f"leg {r.leg.index} ({r.leg.label()}) was not derived")
            continue
        key_of_leg[r.leg.index] = (iid, r.role)
        leg_of_key[(iid, r.role)] = r.leg.index
    return key_of_leg, leg_of_key, problems


def resolve_edges(case: Case, rows, leg_of_key: dict[tuple[str, str], int]):
    """Edges as (unit, from_leg, to_leg). A unit is found by its bytes (``assign_values`` refuses twins); an
    edge whose value is no unit's is kept as ``?value`` (an extra, so red). An edge with an end on no line leg, or
    carrying a label that is not one of its unit's types, is a problem."""
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
        resolved.add((unit, f, to))
        inherited[to].append({"unit": unit, "label": etype, "value": value, "from": f})
    return resolved, raw, inherited, problems


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

    interactions.drain(0)
    lap("interactions")
    data_lineage.drain(0)
    lap("data_lineage")
    detector = classify.load(case.dir)
    classify.check_authored(case, renderer, detector.authored)
    classification.drain(0, detector=detector, model_version=detector.model_version)
    lap("classification")
    label_lineage.drain(0, _label_config())
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
        leg_ready.drain(0, observer=counting)
    finally:
        risk_config.inherited_labels_mode = saved
        leg_ready._label_hold[:] = saved_hold
    lap("leg_ready+risk")

    obs = _read(case, renderer.rendered, dsn, mode)
    obs.timings = timings
    obs.opa_calls = calls["n"]
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

    key_of_leg, leg_of_key, problems = map_legs(rendered, ix_of)
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

    return Observation(mode=mode, legs=legs, edges=resolved, raw_edges=raw, verdict=verdict, timings={},
                       neighbourhood=neighbourhood(case, resolved), opa_calls=0, problems=problems)


def neighbourhood(case: Case, edges: set[tuple[str, int, int]]) -> dict[int, dict[str, str]]:
    """For every unit carried into a leg: of the OTHER units present at the unit's
    origin leg, how many were also carried into this leg. ``k of n``."""
    legs = {leg.index: leg for leg in case.line.legs}
    incoming: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))   # to -> unit -> froms
    for unit, f, to in edges:
        incoming[to][unit].add(f)

    def origins(unit: str, leg: int) -> frozenset[int]:
        """Every leg the unit's edges lead back to that no edge of the unit enters (all routes, no choice)."""
        out, seen, todo = set(), set(), [leg]
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            froms = incoming.get(x, {}).get(unit)
            if froms:
                todo.extend(froms)
            else:
                out.add(x)
        return frozenset(out or {leg})

    out: dict[int, dict[str, str]] = {}
    for to, units in incoming.items():
        for unit in units:
            o = origins(unit, to)
            siblings = sorted({u for leg in o if leg in legs for u in legs[leg].units} - {unit})
            k = sum(1 for s in siblings if s in units and origins(s, to) & o)
            out.setdefault(to, {})[unit] = f"{k} of {len(siblings)}"
    return out
