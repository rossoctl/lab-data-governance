"""Run one case through the real system and read the rows back.

receiver (OTLP gRPC) -> interactions drain -> data-lineage drain -> classification
drain (replayed model output through the production seam) -> label-lineage drain
-> leg-ready drain with the risk observer against a real OPA -> rows.

A case runs its steps (by default: send every leg, then drain): ``send`` puts legs on
the wire, ``drain`` runs every drain from where it stopped, ``derive`` queues a
conversation for derivation again through the production correction queue. A run is
in one of three modes, set through the production switches (:func:`switches`). A case's
``scopes:`` reach label lineage as the production declaration (:func:`declaration`).

A drain may stop early by design (leg-ready holds its cursor while OPA is down) and
returns where it stopped: a run where a drain stopped short of its stream's end or
left work in its queue is red (:func:`unfinished`), whatever the rows say.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

import psycopg

from data_governance import db
from data_governance.processors.classification import driver as classification
from data_governance.processors.data_lineage import driver as data_lineage
from data_governance.processors.data_lineage import memory
from data_governance.processors.interactions import sidecar_driver as interactions
from data_governance.processors.label_lineage import driver as label_lineage
from data_governance.processors.leg_ready import driver as leg_ready
from data_governance.risk import config as risk_config
from data_governance.risk.engine.observer import make_risk_observer
from tools.load_trace import rows_to_request

from . import classify
from .cases import Case, Edge, fmt_edges
from .render import Renderer, Rendered, render

# The production paths a case can need that this branch does not have yet: a case that uses one is held, not run
# (``Case.uses``; its ``expect.pending.held`` names them). The change that builds a path removes its row here and
# applies it in :func:`run_case`, where the comment of each row says.
NOT_ON_BRANCH = {
    "switch": "the cross-conversation switch: set by switches() per mode, and by a cross step",
    "config": "the limits as configuration: set in run_case before the first send",
    "reclassify": "a classification replaced and its conversation derived again: a reclassify step",
    "tiers": "the verdict tiers and decision versions: read by verdict_at",
}

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
    inherited: list[dict[str, Any]]            # edges INTO this leg: unit, value, from_leg, level
    risk: dict[str, Any] | None                # the risk record that evidences this leg (sink only)


@dataclasses.dataclass
class Observation:
    mode: str                                  # "off" | "conv" | "cross"
    legs: dict[int, LegRows]
    edges: set[Edge]                           # (unit, from_leg, to_leg, level) resolved to line legs
    raw_edges: list[dict[str, Any]]
    verdict: str | None                        # at the sink: enforcement_type as the record writes it
    timings: dict[str, float]
    observer_calls: int                        # risk-observer invocations (one per evidence state)
    problems: list[str] = dataclasses.field(default_factory=list)   # rows the judge cannot read: always red
    evidence: dict[int, dict[str, frozenset[str]]] = dataclasses.field(default_factory=dict)  # leg -> unit -> kinds
    marks: dict[int, frozenset[str]] = dataclasses.field(default_factory=dict)              # leg -> marks


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
    own bytes (the suite runs the exact finder), is a problem; so is an edge drawn by more than one row of one label
    type, which the edge set would hide (two origins of one value under two types are two rows by right). A row's level is a crossing's (``None`` for an edge inside one conversation): the crossing
    core writes it, and its row is read here beside the conversation's own."""
    value_unit = {v: u for u, v in case.values.items()}
    rows_of: Counter[tuple[Edge, str]] = Counter()
    raw: list[dict[str, Any]] = []
    inherited: dict[int, list[dict[str, Any]]] = defaultdict(list)
    problems: list[str] = []
    for fi, flt, ti, tlt, etype, value, matched, kind, level in rows:
        f = leg_of_key.get((fi, flt))
        to = leg_of_key.get((ti, tlt))
        unit = value_unit.get(value, f"?{value}")
        raw.append({"from": f, "to": to, "unit": unit, "label": etype, "value": value, "kind": kind, "level": level})
        if f is None or to is None:
            problems.append(f"edge {unit} {f}>{to} ({etype}) has an end on no line leg")
            continue
        if unit in case.accepts and etype not in case.accepts[unit]:
            problems.append(f"edge {unit} {f}>{to} carries {etype}, not one of {sorted(case.accepts[unit])}")
        if kind != "whole" or matched != value:
            problems.append(f"edge {unit} {f}>{to} matched {matched!r} as {kind!r}, not the whole value")
        rows_of[((unit, f, to, level), etype)] += 1
        inherited[to].append({"unit": unit, "label": etype, "value": value, "from": f, "level": level})
    problems += [f"edge {fmt_edges([e])} ({etype}) drawn by {n} rows"
                 for (e, etype), n in sorted(rows_of.items(), key=str) if n > 1]
    return {e for e, _ in rows_of}, raw, inherited, problems


def evidence_at(case: Case, legs: dict[int, "LegRows"]) -> dict[int, dict[str, frozenset[str]]]:
    """leg -> unit -> how the unit is known there, read from the rows: ``classified TYPE`` for every type the model
    gave the unit's own bytes at the leg, and one kind per edge row into it: ``inherited`` for an edge inside the
    conversation, ``crossed L`` for a crossing. A crossed label carried on inside a conversation and
    an ambiguous result are read from the rows that write them; until then they are never observed."""
    out: dict[int, dict[str, frozenset[str]]] = {}
    for i, r in legs.items():
        cells: dict[str, set[str]] = defaultdict(set)
        findings = (r.classification or {}).get("findings") or []
        for unit, value in case.values.items():
            cells[unit] |= {f"classified {t}" for t, text in findings if text == value}
        for e in r.inherited:
            cells[e["unit"]].add(f"crossed {e['level']}" if e["level"] else "inherited")
        out[i] = {u: frozenset(k) for u, k in cells.items() if k}
    return out


# Each drain the run calls and the table whose ``seq`` its cursor walks; the work queues the drains own. With
# inherited labels off, leg-ready keeps a label-only recompute request for a later `on` run: not work left undone.
STREAMS = {"interactions": "spans", "data_lineage": "interaction_legs", "classification": "interaction_payloads",
           "label_lineage": "interaction_legs", "leg_ready": "interaction_legs"}
QUEUES = {"data_lineage": "data_lineage_dirty_traces", "label_lineage": "label_lineage_dirty_traces",
          "leg_ready": "risk_recompute_requests"}
_RECOMPUTE_WORK = {"off": "base_recompute", "conv": "base_recompute OR labels_pending",
                   "cross": "base_recompute OR labels_pending"}


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


@contextlib.contextmanager
def environment(values: dict[str, str]):
    """Set *values* in the environment the processors read, and restore it after."""
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k)
            else:
                os.environ[k] = v


def declaration(case: Case, renderer: Renderer) -> str:
    """The case's ``scopes:`` as ``LABEL_MEMORY_SCOPES``, a JSON object: each party by the natural key the
    derivation gives it (``{}`` for a case without scopes)."""
    return json.dumps({renderer.natural_key(party): scope for party, scope in sorted(case.scopes.items())})


def derived_keys(dsn: str) -> set[str]:
    """The natural key of every entity the run derived."""
    with psycopg.connect(dsn) as conn:
        return {r[0] for r in conn.execute("SELECT natural_key FROM entities").fetchall()}


def undeclared(declared: Mapping[str, str], derived: set[str]) -> list[str]:
    """A key the processor was configured with (*declared*, as it parsed it) that no derived entity has: the
    declaration reached nothing."""
    return [f"scopes declares {key} as {declared[key]}, which is no derived entity"
            for key in sorted(declared) if key not in derived]


@contextlib.contextmanager
def switches(mode: str):
    """The production switches of *mode*, through the environment the processors read: inherited labels off in
    ``off``, on in ``conv`` and ``cross``. The cross-conversation switch arrives with the crossing core, which
    sets it here: on in ``off`` (inherited labels off must stop it) and ``cross``, off in ``conv``. Until
    then ``cross`` runs the branch as ``conv`` does."""
    saved = os.environ.get("RISK_INHERITED_LABELS")
    os.environ["RISK_INHERITED_LABELS"] = "off" if mode == "off" else "on"
    risk_config.inherited_labels_mode.cache_clear()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("RISK_INHERITED_LABELS")
        else:
            os.environ["RISK_INHERITED_LABELS"] = saved
        risk_config.inherited_labels_mode.cache_clear()


def run_case(case: Case, *, dsn: str, grpc_endpoint: str, opa_client, mode: str) -> Observation:
    held = case.uses & set(NOT_ON_BRANCH)
    if held:
        raise ValueError(f"{case.id} needs what this branch lacks: {sorted(held)}; it is held, not run")
    timings: dict[str, float] = defaultdict(float)
    t = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal t
        now = time.perf_counter()
        timings[name] = round(timings[name] + now - t, 4)
        t = now

    reset(dsn)
    db.close_pool()
    db.configure(dsn)
    lap("reset")

    renderer = render(case.id, case.units, case.line)
    detector = classify.load(case.dir)
    classify.check_authored(case, renderer, detector.authored)
    saved_hold = list(leg_ready._label_hold)
    leg_ready._label_hold[:] = [None, 0.0]
    calls = {"n": 0}

    def counting(observe):
        def observer(*a, **k):
            calls["n"] += 1
            return observe(*a, **k)
        return observer

    cursors = {s: 0 for s in STREAMS}
    sent = 0
    # the suite runs the exact finder; the case's scopes, or none, through the processor's own configuration
    with switches(mode), environment({"LABEL_MATCHER": "exact", memory.SCOPES_ENV_VAR: declaration(case, renderer)}):
        config = label_lineage.configured()
        try:
            for step in case.steps:
                if step[0] == "send":
                    rows = renderer.rows_of(step[1])
                    _send(rows, grpc_endpoint)
                    sent += len(rows)
                    _wait_rows(dsn, sent)
                    lap("receiver")
                elif step[0] == "derive":
                    with psycopg.connect(dsn, autocommit=True) as conn:
                        conn.execute("SELECT dg_queue_label_trace(%s)", (renderer.trace_of(step[1]),))
                elif step[0] == "drain":
                    cursors["interactions"] = interactions.drain(cursors["interactions"])
                    lap("interactions")
                    cursors["data_lineage"] = data_lineage.drain(cursors["data_lineage"])
                    lap("data_lineage")
                    cursors["classification"] = classification.drain(
                        cursors["classification"], detector=detector, model_version=detector.model_version)
                    lap("classification")
                    cursors["label_lineage"] = label_lineage.drain(cursors["label_lineage"], config)
                    lap("label_lineage")
                    # made at each drain, once the legs it judges are in (the outage test reads the last leg then)
                    cursors["leg_ready"] = leg_ready.drain(cursors["leg_ready"],
                                                           observer=counting(make_risk_observer(opa_client)))
                    lap("leg_ready+risk")
                else:
                    raise ValueError(f"{case.id}: no {step[0]} step on this branch")   # held above
        finally:
            leg_ready._label_hold[:] = saved_hold

    obs = _read(case, renderer.rendered, dsn, mode)
    obs.problems[:0] = unfinished(cursors, *backlog(dsn, mode)) + undeclared(config.scopes, derived_keys(dsn))
    obs.observer_calls = calls["n"]
    lap("read")
    obs.timings = {**timings, "total": round(sum(timings.values()), 4)}
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
            "entity_type, unit_value, matched_text, match_kind, NULL FROM payload_label_edges").fetchall()
        # NULL: no crossing on this branch; the crossing core adds its rows and their level here
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
    # marks: the common-value count and the incomplete mark are read here from the
    # rows that write them; this branch writes none, so a stated mark is never observed.
    return Observation(mode=mode, legs=legs, edges=resolved, raw_edges=raw, verdict=verdict, timings={},
                       observer_calls=0, problems=problems, evidence=evidence_at(case, legs), marks={})

