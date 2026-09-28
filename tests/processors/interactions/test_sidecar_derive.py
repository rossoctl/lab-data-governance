"""Pure unit tests for the two-span sidecar derivation: anchor detection, row
construction, and the interaction-id rule. No DB. (The classify table's own
tests live in ``tests/test_sidecar_facts.py`` with the shared vocabulary.)
"""

from __future__ import annotations

from data_governance.processors.interactions.procedure import _interaction_id
from data_governance.processors.interactions.sidecar import plan_trace

from . import sidecar_golden as golden


# --- anchors: outbound always; inbound entry only; echo is NOT an anchor ----

def test_golden_anchor_verdicts_including_the_echo():
    plan = plan_trace(golden.TRACE, golden.build_spans())
    assert plan.anchor_ids == golden.ANCHORS
    # The callee-side echo (D1) is inbound but sits under an outbound anchor (B3),
    # so it is NOT an anchor — it is the dedup that keeps the tool call one edge.
    assert golden.D1 not in plan.anchor_ids


def test_entry_is_anchor_despite_dangling_wire_parent():
    """A1's parent is the ghost wire root (never stored): entry detection must
    tolerate a dangling parent (not require parent_id IS NULL)."""
    plan = plan_trace(golden.TRACE, golden.build_spans())
    assert golden.A1 in plan.anchor_ids


# --- row construction on the golden trace ---------------------------------

def _rows_by_anchor(plan):
    return {r.anchor_span_id: r for r in plan.want.values()}


def test_golden_rows_endpoints_and_parenting():
    plan = plan_trace(golden.TRACE, golden.build_spans())
    rows = _rows_by_anchor(plan)
    assert len(rows) == 4

    i1 = rows[golden.A1]
    assert (i1.caller.natural_key, i1.callee.natural_key) == (
        "user:alice", "agent:team1/weather-service")
    assert i1.parent_anchor_span_id is None  # the root

    i3 = rows[golden.B3]
    # Callee identity comes from the echo's self.id (weather-tool), NOT peer.host.
    assert i3.callee.natural_key == "tool:team1/weather-tool"
    assert i3.parent_anchor_span_id == golden.A1

    i2 = rows[golden.B1]
    assert i2.callee.natural_key == f"llm:{golden._LLM_HOST}/qwen2.5:7b"
    assert i2.caller.natural_key == "agent:team1/weather-service"


def test_golden_rows_carry_the_response_span_and_seqs():
    """The plan's arrival facts: each completed row knows its response span id
    and both spans' seqs (leg seq itself is DB-owned at write time)."""
    plan = plan_trace(golden.TRACE, golden.build_spans())
    rows = _rows_by_anchor(plan)
    i1 = rows[golden.A1]
    assert i1.response_span_id == golden.A2
    assert i1.resp_seq is not None and i1.resp_seq >= i1.seq


def test_golden_bodyless_llm_still_complete_row():
    """Strip the llm request body — the interaction is still first-class with a
    NULL request payload (payloads are enrichment, never a drop reason)."""
    spans = golden.build_spans()
    for s in spans:
        if s.span_id == golden.B1:
            s.attributes.pop("input.value")
    plan = plan_trace(golden.TRACE, spans)
    i2 = _rows_by_anchor(plan)[golden.B1]
    assert i2.request_payload is None
    assert i2.callee.natural_key == f"llm:{golden._LLM_HOST}/qwen2.5:7b"  # still complete
    assert i2.error is False  # response outcome=ok


def test_outcome_abandoned_is_error_row():
    spans = golden.build_spans()
    for s in spans:
        if s.span_id == golden.B2:  # the llm#1 response
            s.attributes["lineage.outcome"] = "abandoned"
            s.attributes.pop("output.value", None)
    plan = plan_trace(golden.TRACE, spans)
    i2 = _rows_by_anchor(plan)[golden.B1]
    assert i2.error is True
    assert i2.response_payload is None


def test_lone_request_is_in_flight():
    """No response span for an exchange → error None, no response span id: the
    response leg will simply be absent (in-flight, not failed)."""
    spans = [s for s in golden.build_spans() if s.span_id != golden.B2]
    plan = plan_trace(golden.TRACE, spans)
    i2 = _rows_by_anchor(plan)[golden.B1]
    assert i2.error is None
    assert i2.response_span_id is None
    assert i2.resp_seq is None


def test_connectors_cover_every_non_anchor_span():
    plan = plan_trace(golden.TRACE, golden.build_spans())
    connector_span_ids = {span_id for _owner, span_id in plan.connectors}
    all_ids = {s.span_id for s in golden.build_spans()}
    assert connector_span_ids == all_ids - plan.anchor_ids  # 6 connectors
    # The echo pair both belong to the tool interaction (B3).
    owners = dict((sid, owner) for owner, sid in plan.connectors)
    assert owners[golden.D1] == golden.B3
    assert owners[golden.D2] == golden.B3


def test_interaction_id_is_trace_slash_exchange():
    assert _interaction_id(golden.TRACE, golden.A1).count("-") == 4  # a uuid
    # Deterministic: same inputs → same id.
    assert _interaction_id("t", "x") == _interaction_id("t", "x")


# --- loud failures on producer contract violations (audit 2026-08-02) ------

import pytest


def test_unknown_lineage_role_raises():
    """A span carrying an exchange id but an unknown role is contractually a
    sidecar span the derivation doesn't understand — never silently skipped."""
    spans = golden.build_spans()
    spans[0].attributes["lineage.role"] = "heartbeat"
    with pytest.raises(ValueError, match="lineage.role"):
        plan_trace(golden.TRACE, spans)


def test_garbled_direction_on_request_raises():
    """A request span with an invalid direction would otherwise fall out of
    every anchor set and the exchange would silently not derive."""
    spans = golden.build_spans()
    req = next(s for s in spans if s.attributes.get("lineage.role") == "request")
    req.attributes["lineage.direction"] = "sideways"
    with pytest.raises(ValueError, match="lineage.direction"):
        plan_trace(golden.TRACE, spans)


def test_missing_self_id_on_anchor_raises():
    """lineage.self.id is contract-unconditional; minting agent:(unknown) would
    weld every broken pod into one shared entity."""
    spans = golden.build_spans()
    outb = next(
        s for s in spans
        if s.attributes.get("lineage.role") == "request"
        and s.attributes.get("lineage.direction") == "outbound"
    )
    del outb.attributes["lineage.self.id"]
    with pytest.raises(ValueError, match="lineage.self.id"):
        plan_trace(golden.TRACE, spans)


def test_missing_outcome_and_error_projection_yields_none_not_false():
    """"Could not determine the outcome" must never be recorded as "succeeded":
    with no lineage.outcome and no OTEL error projection, error is None."""
    import dataclasses

    spans = golden.build_spans()
    idx, resp = next(
        (i, s) for i, s in enumerate(spans)
        if s.attributes.get("lineage.role") == "response"
        and s.attributes.get("lineage.exchange.id") in golden.ANCHORS
    )
    resp.attributes.pop("lineage.outcome", None)
    spans[idx] = dataclasses.replace(resp, error=None)
    xid = resp.attributes["lineage.exchange.id"]
    plan = plan_trace(golden.TRACE, spans)
    row = next(r for r in plan.want.values() if r.anchor_span_id == xid)
    assert row.error is None


# --- the calling pod's kind comes from its own inbound, not the table --------

_E1, _E2 = "e1e1e1e1e1e1e1e1", "e2e2e2e2e2e2e2e2"  # weather-tool → weather http service
_E3, _E4 = "e3e3e3e3e3e3e3e3", "e4e4e4e4e4e4e4e4"  # weather-tool → notifier (a2a)
_F1 = "f1f1f1f1f1f1f1f1"                          # notifier's callee-side echo


def _tool_egress_spans(order=None):
    """The golden trace plus what a tool with its own egress emits: under the
    tool's inbound echo (D1), an outbound http to a plain service and an
    outbound a2a to a peer agent (with the peer's echo). Every attribute is a
    wire fact the sidecar emits; nothing says "I am a tool"."""
    from tests.processors.interactions import sidecar_golden as g

    extra = [
        (_E1, g.D1, "CLIENT", {
            "lineage.role": "request", "lineage.direction": "outbound",
            "lineage.protocol": "http", "lineage.exchange.id": _E1,
            "lineage.self.id": "weather-tool", "lineage.self.namespace": "team1",
            "lineage.peer.host": "weather.example:80", "url.path": "/forecast",
        }),
        (_E2, _E1, "CLIENT", {
            "lineage.role": "response", "lineage.direction": "outbound",
            "lineage.protocol": "http", "lineage.exchange.id": _E1,
            "lineage.self.id": "weather-tool", "lineage.self.namespace": "team1", "lineage.outcome": "ok",
        }),
        (_E3, g.D1, "CLIENT", {
            "lineage.role": "request", "lineage.direction": "outbound",
            "lineage.protocol": "a2a", "lineage.exchange.id": _E3,
            "lineage.self.id": "weather-tool", "lineage.self.namespace": "team1",
            "lineage.peer.host": "notifier.team1.svc:8080", "a2a.method": "message/send",
            "input.value": "forecast ready",
        }),
        (_E4, _E3, "CLIENT", {
            "lineage.role": "response", "lineage.direction": "outbound",
            "lineage.protocol": "a2a", "lineage.exchange.id": _E3,
            "lineage.self.id": "weather-tool", "lineage.self.namespace": "team1", "lineage.outcome": "ok",
            "output.value": "noted",
        }),
        (_F1, _E3, "SERVER", {
            "lineage.role": "request", "lineage.direction": "inbound",
            "lineage.protocol": "a2a", "lineage.exchange.id": _F1,
            "lineage.self.id": "notifier", "lineage.self.namespace": "team1", "a2a.method": "message/send",
            "input.value": "forecast ready",
        }),
    ]
    rows = list(g.GOLDEN) + extra
    by_id = {r[0]: r for r in rows}
    order = order or [r[0] for r in rows]
    seq_of = {sid: i + 1 for i, sid in enumerate(order)}
    from data_governance.retrieval import Span
    return [
        Span(seq=seq_of[sid], trace_id=g.TRACE, span_id=sid, parent_id=by_id[sid][1],
             name=sid, started_at=g._T0, ended_at=g._T0, attributes=dict(by_id[sid][3]),
             observed_at=g._T0, arrival_seq=seq_of[sid], kind=by_id[sid][2], error=False)
        for sid in order
    ]


def test_tool_with_its_own_egress_is_one_entity_not_two():
    """A tool's outbound hops are minted as tool:<self>, not agent:<self>: the
    pod's kind comes from its own inbound in the trace (an inbound mcp makes
    it a tool), not from the outbound rows of the kind table. Both protocols
    a tool might call out with — plain http to a service, a2a to a peer."""
    plan = plan_trace(golden.TRACE, _tool_egress_spans())
    rows = _rows_by_anchor(plan)
    assert rows[_E1].caller.natural_key == "tool:team1/weather-tool"
    assert rows[_E1].callee.natural_key == "service:weather.example:80"
    assert rows[_E3].caller.natural_key == "tool:team1/weather-tool"
    assert rows[_E3].callee.natural_key == "agent:team1/notifier"  # from the echo
    # Both hang under the tool's inbound echo's anchor, i.e. the agent's call.
    assert rows[_E1].parent_anchor_span_id == golden.B3
    assert rows[_E3].parent_anchor_span_id == golden.B3
    # The trace's entity set names weather-tool exactly once.
    keys = {r.caller.natural_key for r in rows.values()} | {r.callee.natural_key for r in rows.values()}
    assert {k for k in keys if k.endswith("/weather-tool")} == {"tool:team1/weather-tool"}
    # And the agent is still the agent.
    assert rows[golden.B1].caller.natural_key == "agent:team1/weather-service"


def test_tool_egress_kind_is_order_independent():
    """The kind is read off the whole trace: the tool's egress arriving BEFORE
    the tool's own inbound (or anything else) derives the same entity."""
    baseline = plan_trace(golden.TRACE, _tool_egress_spans())
    ids = [s.span_id for s in _tool_egress_spans()]
    for order in (list(reversed(ids)), [_E1, _E2, _E3, _E4, _F1] + ids[:10]):
        plan = plan_trace(golden.TRACE, _tool_egress_spans(order))
        rows = _rows_by_anchor(plan)
        assert rows[_E1].caller.natural_key == "tool:team1/weather-tool"
        assert rows[_E3].caller.natural_key == "tool:team1/weather-tool"
        assert {r.caller.natural_key for r in rows.values()} == {
            r.caller.natural_key for r in _rows_by_anchor(baseline).values()}


def _lone(*sids):
    """Just those exchanges of the tool-egress fixture, re-rooted: the trace of a
    pod that served nothing here (an un-propagated escaped hop, or a sidecar
    with no inbound pipeline)."""
    import dataclasses
    root = sids[0]
    keep = [s for s in _tool_egress_spans() if s.span_id in sids]
    return [dataclasses.replace(s, parent_id=None if s.span_id == root else root) for s in keep]


def test_served_nothing_but_sent_a2a_is_an_agent():
    """No inbound for the self in the trace, but it sent a2a: it behaves like an
    agent, so it is one (sent-protocol is the second signal)."""
    rows = _rows_by_anchor(plan_trace(golden.TRACE, _lone(_E3, _E4)))
    assert rows[_E3].caller.natural_key == "agent:team1/weather-tool"


def test_served_nothing_and_sent_only_http_is_undecided_falls_to_default():
    """No inbound and only plain http sent: nothing behavioural types the pod.
    ``entity_kind`` has no undecided value, so this falls through to the
    table's outbound default — pinned here so the day the vocabulary grows,
    this is the assertion to flip."""
    rows = _rows_by_anchor(plan_trace(golden.TRACE, _lone(_E1, _E2)))
    assert rows[_E1].caller.natural_key == "agent:team1/weather-tool"


def test_served_role_beats_sent_protocol():
    """A pod that serves mcp AND sends a2a is a tool that delegates (Igor's
    create-booking case), on both ends of every exchange it is part of."""
    rows = _rows_by_anchor(plan_trace(golden.TRACE, _tool_egress_spans()))
    assert rows[_E3].caller.natural_key == "tool:team1/weather-tool"   # its a2a egress
    assert rows[golden.B3].callee.natural_key == "tool:team1/weather-tool"  # the call it served


def test_pod_serving_both_a2a_and_mcp_is_an_agent():
    """Conflicting inbound evidence (the same self answers a2a AND mcp) resolves
    to `agent` deterministically — the table's own default — never to whichever
    inbound happened to arrive first."""
    spans = _tool_egress_spans()
    # Make weather-tool ALSO answer an a2a call: retag the notifier echo onto it.
    for s in spans:
        if s.span_id == _F1:
            s.attributes["lineage.self.id"] = "weather-tool"
    rows = _rows_by_anchor(plan_trace(golden.TRACE, spans))
    assert rows[_E1].caller.natural_key == "agent:team1/weather-tool"
    assert rows[_E3].caller.natural_key == "agent:team1/weather-tool"
    assert rows[_E3].callee.natural_key == "agent:team1/weather-tool"  # echo self.id wins as before


# --- namespace: the other half of a pod's identity (contract v1.7, §7) -------

def test_same_self_id_in_two_namespaces_is_two_entities():
    """The bug v1.7 fixes: ``self.id`` is the last segment of a SPIFFE ID, so a
    ``weather-service`` in team1 and another in team2 keyed one entity. With the
    namespace inside the natural key they are two rows with two uuid5 ids —
    here the tool pod is renamed to the agent's name but lives in team2."""
    spans = golden.build_spans()
    for s in spans:
        if s.span_id in (golden.D1, golden.D2):
            s.attributes["lineage.self.id"] = "weather-service"
            s.attributes["lineage.self.namespace"] = "team2"
    plan = plan_trace(golden.TRACE, spans)
    rows = _rows_by_anchor(plan)
    assert rows[golden.A1].callee.natural_key == "agent:team1/weather-service"
    assert rows[golden.B3].callee.natural_key == "tool:team2/weather-service"
    assert rows[golden.B3].callee.namespace == "team2"
    # _self_kinds is per pod, not per name: team2's served-mcp verdict (tool)
    # does not leak onto team1's outbound caller.
    assert rows[golden.B1].caller.natural_key == "agent:team1/weather-service"
    keys = {e.natural_key for r in plan.want.values() for e in (r.caller, r.callee)}
    assert {k for k in keys if k.endswith("/weather-service")} == {
        "agent:team1/weather-service", "tool:team2/weather-service"}
    from data_governance.processors.interactions.procedure import _entity_id
    assert _entity_id("agent:team1/weather-service") != _entity_id("agent:team2/weather-service")


def test_echo_carries_the_callee_namespace_not_the_callers():
    """A cross-namespace call: the caller is in team1, the callee's own echo
    span says team2 — the callee entity takes the echo's (namespace, self.id);
    ``peer.host`` is not consulted and the caller's namespace is not assumed."""
    spans = golden.build_spans()
    for s in spans:
        if s.span_id in (golden.D1, golden.D2):
            s.attributes["lineage.self.namespace"] = "team2"
    plan = plan_trace(golden.TRACE, spans)
    callee = _rows_by_anchor(plan)[golden.B3].callee
    assert (callee.namespace, callee.ident, callee.natural_key) == (
        "team2", "weather-tool", "tool:team2/weather-tool")


def test_pre_v17_spans_key_without_a_namespace():
    """Spans a v1.6 producer emitted carry no ``lineage.self.namespace``; stored
    and replayable, they derive exactly as before — namespace None, the
    un-namespaced key. Absence is recorded, never filled in from ``peer.host``,
    the SPIFFE path or the rest of the trace."""
    spans = golden.build_spans()
    for s in spans:
        s.attributes.pop("lineage.self.namespace", None)
    plan = plan_trace(golden.TRACE, spans)
    rows = _rows_by_anchor(plan)
    assert rows[golden.A1].callee.natural_key == "agent:weather-service"
    assert rows[golden.A1].callee.namespace is None
    assert rows[golden.B3].callee.natural_key == "tool:weather-tool"


def test_non_pod_entities_have_no_namespace():
    """Only entities that ARE a pod carry a namespace: a user, an anonymous
    client, an LLM endpoint and a ``peer.host`` fallback have none."""
    plan = plan_trace(golden.TRACE, golden.build_spans())
    rows = _rows_by_anchor(plan)
    assert rows[golden.A1].caller.namespace is None     # user:alice
    assert rows[golden.B1].callee.namespace is None     # the llm endpoint
    assert rows[golden.B1].caller.namespace == "team1"  # the agent pod
    spans = [s for s in golden.build_spans() if s.span_id not in (golden.D1, golden.D2)]
    fallback = _rows_by_anchor(plan_trace(golden.TRACE, spans))[golden.B3].callee
    assert fallback.namespace is None                   # no echo → peer.host, no namespace
    assert fallback.natural_key == "tool:weather-tool-mcp.team1.svc:8000"


def test_present_but_malformed_namespace_is_a_contract_violation():
    """A v1.7 producer refuses to start on anything but a DNS label, so such a
    value on the wire is a violation, not absence — folding it into "absent"
    would re-weld the pod onto the un-namespaced row. Die loudly, like a
    missing self.id."""
    import pytest
    for bad in ("", " team1 ", "TEAM1", "team1/team2", "team1.svc"):
        spans = golden.build_spans()
        for s in spans:
            if s.span_id == golden.A1:
                s.attributes["lineage.self.namespace"] = bad
        with pytest.raises(ValueError, match="lineage.self.namespace"):
            plan_trace(golden.TRACE, spans)


def test_mixed_rollout_keys_a_pod_twice_and_says_so():
    """During a rolling upgrade a pod's spans may disagree with themselves: its
    inbound (a v1.7 sidecar) carries the fact while an older outbound record
    did not, or a v1.6 callee echoes under a v1.7 caller. The consumer records
    what each span says — two rows for one pod, for as long as both trace
    sets exist (contract §7). Pinned so the behaviour is a stated consequence,
    not a surprise."""
    spans = golden.build_spans()
    for s in spans:
        if s.span_id in (golden.D1, golden.D2):       # the tool's own echo, v1.6-style
            s.attributes.pop("lineage.self.namespace", None)
    rows = _rows_by_anchor(plan_trace(golden.TRACE, spans))
    assert rows[golden.B3].callee.natural_key == "tool:weather-tool"   # from the v1.6 echo
    spans = golden.build_spans()
    for s in spans:
        if s.span_id in (golden.B1, golden.B2, golden.B3, golden.B4, golden.B5, golden.B6):
            s.attributes.pop("lineage.self.namespace", None)           # the agent's outbounds, v1.6-style
    rows = _rows_by_anchor(plan_trace(golden.TRACE, spans))
    assert rows[golden.A1].callee.natural_key == "agent:team1/weather-service"  # its inbound, v1.7
    assert rows[golden.B1].caller.natural_key == "agent:weather-service"        # its outbounds, v1.6
    assert rows[golden.B1].caller.kind == "agent"  # verdict falls to the table default per pod-key
