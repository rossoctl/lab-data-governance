"""In-process tests for the per-leg **Labels** read (``retrieval.get_leg_labels``)
against a migrated DB.

The seed is a cut of a live reference trace (e06cd67d…): the
notify-partner interaction (request seq 92738, its echo response 92739) and two
of the origins whose labels reached it — the LLM response that wrote the booking
reference (91707, OPID) and the create-booking response that issued it (90574,
PID). The merge itself is covered without a DB in ``test_label_merge.py``; these
pin what only the SQL decides: pinning to the current payload, where the origin's
``seq`` comes from, trace scoping, and the shapes before migration 0024.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from data_governance import db, retrieval
from data_governance.retrieval import labels

_TID = "e06cd67dedd004056a388a8ac48842a0"
_IX = "f48567d1-a81a-5b37-a96e-022d4729b829"
_LLM = "c0f9059b-c66e-5fde-b19b-32537fe8a8f8"
_BOOKING = "efc92832-1b3b-581c-9aab-431ff95518fd"
_REQ_HASH = "464d6021"
_RES_HASH = "1df1122b"
_FINDINGS_REQ = [{"entity_type": "OPID", "start": 16, "end": 25, "text": "BK-2850EB",
                  "sensitivity_level": "INTERNAL", "regulatory_tags": ["PI"]}]
_FINDINGS_RES = [{"entity_type": "OPID", "start": 29, "end": 38, "text": "BK-2850EB",
                  "sensitivity_level": "INTERNAL", "regulatory_tags": ["PI"]}]


def _interaction(conn, ix_id: str, trace_id: str, caller: str, callee: str,
                 legs: list[tuple[str, str | None, int]]) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
        "VALUES (%s, %s, %s, %s, 'x')",
        (ix_id, trace_id, caller, callee),
    )
    for leg_type, payload_hash, seq in legs:
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, payload_hash, error, seq) "
            "VALUES (%s, %s, now(), %s, false, %s)",
            (ix_id, leg_type, payload_hash, seq),
        )


def _edge(conn, *, to_leg: str, to_hash: str, origin: str, origin_leg: str = "response",
          entity_type: str, tags: list[str], level: str, span: tuple[int, int],
          trace_id: str = _TID, to_ix: str = _IX) -> None:
    conn.execute(
        "INSERT INTO payload_label_edges (edge_id, trace_id, to_interaction_id, to_leg_type, "
        "to_payload_hash, from_interaction_id, from_leg_type, from_payload_hash, "
        "origin_interaction_id, origin_leg_type, origin_payload_hash, unit_start, unit_end, "
        "unit_text, unit_value, entity_type, regulatory_tags, sensitivity_level, matched_text, "
        "matched_start, matched_end, matcher, match_kind, score, held_from, held_to, seq) "
        "VALUES (%s, %s, %s, %s, %s, 'e39b9919', 'request', '3efe7b12', %s, %s, 'origin-hash', "
        "0, 9, 'BK-2850EB', 'BK-2850EB', %s, %s, %s, 'BK-2850EB', %s, %s, 'exact', 'whole', 1, "
        "0, 9, 92738)",
        (f"{to_ix}/{to_leg}/{to_hash}/{origin}/{entity_type}", trace_id, to_ix, to_leg, to_hash,
         origin, origin_leg, entity_type, tags, level, span[0], span[1]),
    )


@pytest.fixture()
def seeded(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        for eid, name in [("e-ta", "travel-advisor"), ("e-np", "notify-partner"),
                          ("e-ba", "booking-agent"), ("e-llm", "qwen2.5:7b"),
                          ("e-cb", "create-booking")]:
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, "
                "original_seq) VALUES (%s, 'agent', %s, %s, 'span-attr', 1)",
                (eid, f"nk-{eid}", name),
            )
        _interaction(conn, _IX, _TID, "e-ta", "e-np",
                     [("request", _REQ_HASH, 92738), ("response", _RES_HASH, 92739)])
        _interaction(conn, _LLM, _TID, "e-ba", "e-llm", [("request", "llm-req", 90593),
                                                       ("response", "llm-res", 91707)])
        _interaction(conn, _BOOKING, _TID, "e-ba", "e-cb", [("request", "cb-req", 90570),
                                                          ("response", "cb-res", 90574)])
        for h, findings in [(_REQ_HASH, _FINDINGS_REQ), (_RES_HASH, _FINDINGS_RES)]:
            conn.execute(
                "INSERT INTO payload_classifications (content_hash, sensitivity_level, "
                "regulatory_tags, findings, model_version) VALUES (%s, 'INTERNAL', '{PI}', %s::jsonb, 2)",
                (h, json.dumps(findings)),
            )
        _edge(conn, to_leg="request", to_hash=_REQ_HASH, origin=_LLM, entity_type="OPID",
              tags=["PI"], level="INTERNAL", span=(16, 25))
        _edge(conn, to_leg="request", to_hash=_REQ_HASH, origin=_BOOKING, entity_type="PID",
              tags=["PII"], level="RESTRICTED", span=(16, 25))
        _edge(conn, to_leg="response", to_hash=_RES_HASH, origin=_BOOKING, entity_type="PID",
              tags=["PII"], level="RESTRICTED", span=(29, 38))
        conn.execute(
            "INSERT INTO label_lineage_trace_status (trace_id, status, unclassified_legs, "
            "unanchored_findings, derived_legs, edge_count, matcher) "
            "VALUES (%s, 'complete', 0, 0, 6, 3, 'exact')",
            (_TID,),
        )
        conn.commit()
    return _TID


def _legs(result) -> dict:
    return {leg.leg_type: leg for leg in result.legs}


def test_reference_request_leg(seeded: str) -> None:
    result = retrieval.get_leg_labels(seeded, _IX)
    assert result.lineage_status == "complete"
    req = _legs(result)["request"]
    assert (req.payload_hash, req.seq) == (_REQ_HASH, 92738)
    assert (req.found.sensitivity_level, req.found.regulatory_tags) == ("INTERNAL", ["PI"])
    assert (req.carried.max_level, req.carried.regulatory_tags, req.carried.origin_count) == (
        "RESTRICTED", ["PI", "PII"], 2)
    assert [(r.entity_type, r.found_here) for r in req.labels] == [("PID", False), ("OPID", True)]
    pid = req.labels[0].carried_in[0]
    # The origin's seq is its own leg's, never the edge's seq (the to leg's, 92738).
    assert (pid.origin_seq, pid.caller, pid.callee, pid.match_kind) == (
        90574, "booking-agent", "create-booking", "whole")


def test_the_response_echo_has_its_own_offsets(seeded: str) -> None:
    res = _legs(retrieval.get_leg_labels(seeded, _IX))["response"]
    assert [(r.entity_type, r.start, r.end) for r in res.labels] == [("PID", 29, 38), ("OPID", 29, 38)]
    assert res.carried.origin_count == 1


def test_an_edge_at_a_stale_payload_is_not_read(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        _edge(conn, to_leg="request", to_hash="an-older-payload", origin=_LLM, entity_type="SSN",
              tags=["PII"], level="RESTRICTED", span=(0, 4))
        conn.commit()
    req = _legs(retrieval.get_leg_labels(seeded, _IX))["request"]
    assert "SSN" not in {r.entity_type for r in req.labels}


def test_a_missing_origin_leg_reads_as_nulls(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM interaction_legs WHERE interaction_id = %s", (_BOOKING,))
        conn.execute("DELETE FROM interactions WHERE id = %s", (_BOOKING,))
        conn.commit()
    pid = _legs(retrieval.get_leg_labels(seeded, _IX))["request"].labels[0].carried_in[0]
    assert (pid.origin_interaction_id, pid.origin_seq, pid.caller, pid.callee) == (
        _BOOKING, None, None, None)


def test_an_origin_in_another_trace_names_that_trace(seeded: str, configured_db: str) -> None:
    # The exact finder writes none; the shape carries it for a later finder.
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE payload_label_edges SET origin_trace_id = 'an-earlier-trace' "
                     "WHERE origin_interaction_id = %s", (_BOOKING,))
        conn.commit()
    pid, opid = _legs(retrieval.get_leg_labels(seeded, _IX))["request"].labels
    assert (pid.carried_in[0].origin_trace_id, opid.carried_in[0].origin_trace_id) == (
        "an-earlier-trace", None)


def test_an_unclassified_leg_lists_what_was_carried_in(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM payload_classifications WHERE content_hash = %s", (_REQ_HASH,))
        conn.commit()
    req = _legs(retrieval.get_leg_labels(seeded, _IX))["request"]
    assert req.found is None
    assert [(r.entity_type, r.found_here) for r in req.labels] == [("PID", False), ("OPID", False)]


def test_a_leg_without_a_payload_is_omitted(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "UPDATE interaction_legs SET payload_hash = NULL "
            "WHERE interaction_id = %s AND leg_type = 'response'",
            (_IX,),
        )
        conn.commit()
    assert [leg.leg_type for leg in retrieval.get_leg_labels(seeded, _IX).legs] == ["request"]


def test_unknown_or_foreign_interaction_is_empty(seeded: str) -> None:
    assert retrieval.get_leg_labels(seeded, "no-such-interaction") == retrieval.GetLegLabelsResult()
    # A real interaction id under another trace id must not answer.
    assert retrieval.get_leg_labels("another-trace", _IX).legs == []


def test_partial_status_passes_through(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE label_lineage_trace_status SET status = 'partial'")
        conn.commit()
    assert retrieval.get_leg_labels(seeded, _IX).lineage_status == "partial"


def test_not_derived_trace_has_no_status(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("DELETE FROM label_lineage_trace_status")
        conn.commit()
    assert retrieval.get_leg_labels(seeded, _IX).lineage_status is None


def test_before_label_lineage_found_here_only(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("DROP TABLE payload_label_edges, label_lineage_trace_status")
        conn.commit()
    result = retrieval.get_leg_labels(seeded, _IX)
    assert result.lineage_status is None
    req = _legs(result)["request"]
    assert req.carried is None
    assert [(r.entity_type, r.found_here, r.carried_in) for r in req.labels] == [("OPID", True, [])]


def test_before_the_interactions_migration_empty(seeded: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS interaction_spans, interaction_legs, entity_spans, "
            "interaction_payloads, interactions, entities CASCADE"
        )
        conn.commit()
    assert retrieval.get_leg_labels(seeded, _IX).legs == []


# ---------------------------------------------------------------------------
# Chains: the lineage of every carried-in value
# ---------------------------------------------------------------------------
#
# The seed is a cut of a live booking trace (trace 98134844…): the
# partner request 83053 and the legs the booking reference travelled before it.
# From the live edges: every edge into 83053 comes from 82049 (notify-partner's
# request); into 82049 the PID comes twice, from create-booking's response 80565
# (the origin) and from the LLM response 82082, which got it from the LLM request
# 80603, which got it from 80565. So the PID has two routes, the OPID (born at
# 82082) one; the read lists one shortest route per edge into the leg and counts
# them all.

_B12 = "98134844c66d7a7996303b049d6fc350"
_P = "02ef1eb2"      # notify-partner → partner (83053 request, 83054 response)
_NP = "27f8c65f"     # booking-agent → notify-partner (82049 request)
_LLM2 = "be761eee"   # booking-agent → LLM (80603 request, 82082 response)
_CB = "bf377f41"     # booking-agent → create-booking (80565 response)
_CB_TEXT = '{"id": "BK-83CE5F", "status": "confirmed", "guest_name": "<name>"}'
_UNIT = (_CB_TEXT.index("BK-83CE5F"), _CB_TEXT.index("BK-83CE5F") + 9)


def _edge2(conn, *, eid: str, trace: str, to: tuple[str, str, str], frm: tuple[str, str, str],
           origin: tuple[str, str, str], unit: tuple[int, int], entity_type: str, tags: list[str],
           level: str, span: tuple[int, int] | None, from_trace: str | None = None,
           origin_trace: str | None = None, value: str = "BK-83CE5F", seq: int = 0) -> None:
    conn.execute(
        "INSERT INTO payload_label_edges (edge_id, trace_id, to_interaction_id, to_leg_type, "
        "to_payload_hash, from_interaction_id, from_leg_type, from_payload_hash, from_trace_id, "
        "origin_interaction_id, origin_leg_type, origin_payload_hash, origin_trace_id, "
        "unit_start, unit_end, unit_text, unit_value, entity_type, regulatory_tags, "
        "sensitivity_level, matched_text, matched_start, matched_end, matcher, match_kind, "
        "score, held_from, held_to, seq) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, 'exact', 'whole', 1, 0, %s, %s)",
        (eid, trace, *to, *frm, from_trace, *origin, origin_trace, unit[0], unit[1], value, value,
         entity_type, tags, level, None if span is None else value,
         None if span is None else span[0], None if span is None else span[1], len(value), seq),
    )


@pytest.fixture()
def booking_trace(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        for eid, name in [("e-ba", "booking-agent"), ("e-np", "notify-partner"),
                          ("e-llm", "qwen2.5:7b"), ("e-cb", "create-booking"),
                          ("e-partner", "api.travel-partner.example:8000")]:
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, "
                "original_seq) VALUES (%s, 'agent', %s, %s, 'span-attr', 1)",
                (eid, f"nk-{eid}", name),
            )
        _interaction(conn, _CB, _B12, "e-ba", "e-cb", [("request", "cb-req", 79794),
                                                    ("response", "cb-res", 80565)])
        _interaction(conn, _LLM2, _B12, "e-ba", "e-llm", [("request", "llm-req", 80603),
                                                       ("response", "llm-res", 82082)])
        _interaction(conn, _NP, _B12, "e-ba", "e-np", [("request", "np-req", 82049),
                                                    ("response", "np-res", 82050)])
        _interaction(conn, _P, _B12, "e-np", "e-partner", [("request", "p-req", 83053),
                                                        ("response", "p-res", 83054)])
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES ('cb-res', %s::jsonb, 1)", (json.dumps(_CB_TEXT),),
        )
        for h, findings in [
            ("p-req", [{"entity_type": "OPID", "start": 16, "end": 25, "text": "BK-83CE5F",
                        "sensitivity_level": "INTERNAL", "regulatory_tags": ["PI"]}]),
            # The echo: classified, a finding, and nothing carried in (seeded so).
            ("p-res", [{"entity_type": "OPID", "start": 29, "end": 38, "text": "BK-83CE5F",
                        "sensitivity_level": "INTERNAL", "regulatory_tags": ["PI"]}]),
        ]:
            conn.execute(
                "INSERT INTO payload_classifications (content_hash, sensitivity_level, "
                "regulatory_tags, findings, model_version) VALUES (%s, 'INTERNAL', '{PI}', %s::jsonb, 2)",
                (h, json.dumps(findings)),
            )
        cb = (_CB, "response", "cb-res")
        llm_req, llm_res = (_LLM2, "request", "llm-req"), (_LLM2, "response", "llm-res")
        np_req, p_req = (_NP, "request", "np-req"), (_P, "request", "p-req")
        pid = dict(origin=cb, unit=_UNIT, entity_type="PID", tags=["PII"], level="RESTRICTED")
        _edge2(conn, eid="pid-83053", trace=_B12, to=p_req, frm=np_req, span=(16, 25), seq=83053, **pid)
        _edge2(conn, eid="pid-82049-direct", trace=_B12, to=np_req, frm=cb, span=(16, 25), seq=82049, **pid)
        _edge2(conn, eid="pid-82049-llm", trace=_B12, to=np_req, frm=llm_res, span=(16, 25), seq=82049, **pid)
        _edge2(conn, eid="pid-82082", trace=_B12, to=llm_res, frm=llm_req, span=(7, 16), seq=82082, **pid)
        _edge2(conn, eid="pid-80603", trace=_B12, to=llm_req, frm=cb, span=(2396, 2405), seq=80603, **pid)
        opid = dict(origin=llm_res, unit=(7, 16), entity_type="OPID", tags=["PI"], level="INTERNAL")
        _edge2(conn, eid="opid-83053", trace=_B12, to=p_req, frm=np_req, span=(16, 25), seq=83053, **opid)
        _edge2(conn, eid="opid-82049", trace=_B12, to=np_req, frm=llm_res, span=(16, 25), seq=82049, **opid)
        conn.execute(
            "INSERT INTO label_lineage_trace_status (trace_id, status, unclassified_legs, "
            "unanchored_findings, derived_legs, edge_count, matcher) "
            "VALUES (%s, 'complete', 0, 0, 8, 7, 'exact')",
            (_B12,),
        )
        conn.commit()
    return _B12


def _route(chain) -> list[tuple]:
    return [(h.seq, h.role) for h in chain]


def test_the_partner_request_pid_has_one_shortest_chain_and_counts_two_routes(booking_trace: str) -> None:
    req = _legs(retrieval.get_leg_labels(booking_trace, _P))["request"]
    pid, opid = req.labels
    assert (pid.entity_type, pid.found_here, opid.entity_type, opid.found_here) == (
        "PID", False, "OPID", True)
    (origin,) = pid.carried_in
    assert origin.origin_seq == 80565
    # One edge into 83053, so one chain: the shortest route. The route via the
    # LLM (80603 → 82082) is counted, not listed.
    assert [_route(c) for c in origin.chains] == [
        [(80565, "origin"), (82049, "hop"), (83053, "here")],
    ]
    assert origin.route_count == 2
    # Tracking lights every leg on any route, so the LLM round trip the chain
    # skips (80603, 82082) is a carrier.
    assert [(c.interaction_id, c.leg_type) for c in origin.carriers] == [
        (_P, "request"), (_NP, "request"), (_LLM2, "request"), (_LLM2, "response"), (_CB, "response")]
    assert {c.trace_id for c in origin.carriers} == {booking_trace}
    born = origin.chains[0][0]
    assert (born.interaction_id, born.leg_type, born.trace_id) == (_CB, "response", booking_trace)
    assert (born.caller, born.callee) == ("booking-agent", "create-booking")
    assert (born.text, born.start, born.end) == ("BK-83CE5F", *_UNIT)
    # The origin's excerpt is the projected payload around the unit, the unit
    # re-based into it; this payload is short, so no ellipsis.
    assert born.excerpt.text == _CB_TEXT
    assert born.excerpt.text[born.excerpt.start:born.excerpt.end] == "BK-83CE5F"
    here = origin.chains[0][-1]
    assert (here.interaction_id, here.leg_type, here.start, here.end) == (_P, "request", 16, 25)


def test_the_partner_request_opid_born_at_the_llm_has_one_chain(booking_trace: str) -> None:
    req = _legs(retrieval.get_leg_labels(booking_trace, _P))["request"]
    (origin,) = req.labels[1].carried_in
    assert origin.origin_seq == 82082
    assert [_route(c) for c in origin.chains] == [[(82082, "origin"), (82049, "hop"), (83053, "here")]]
    assert origin.route_count == 1
    # Not every leg of the trace: only those on the OPID's one route.
    assert [(c.interaction_id, c.leg_type) for c in origin.carriers] == [
        (_P, "request"), (_NP, "request"), (_LLM2, "response")]
    # The LLM response has no stored payload here: the hop stands, the excerpt does not.
    assert origin.chains[0][0].excerpt is None


def test_the_partner_response_found_here_only_leg_gains_nothing(booking_trace: str) -> None:
    res = _legs(retrieval.get_leg_labels(booking_trace, _P))["response"]
    assert res.carried is None
    (opid,) = res.labels
    assert (opid.found_here, opid.carried_in) == (True, [])


def test_two_edges_of_one_origin_into_one_leg_are_one_origin_with_both_chains(
        booking_trace: str, configured_db: str) -> None:
    # Into 82049 the PID arrives twice (direct and via the LLM): one origin, one
    # chain per edge into the leg, two routes.
    req = _legs(retrieval.get_leg_labels(booking_trace, _NP))["request"]
    (origin,) = req.labels[0].carried_in
    assert req.labels[0].entity_type == "PID" and req.carried.origin_count == 2
    assert [_route(c) for c in origin.chains] == [
        [(80565, "origin"), (82049, "here")],
        [(80565, "origin"), (80603, "hop"), (82082, "hop"), (82049, "here")],
    ]
    # Carriers show the span the finder matched at THEIR payload.
    llm_req = origin.chains[1][1]
    assert (llm_req.text, llm_req.start, llm_req.end, llm_req.excerpt) == ("BK-83CE5F", 2396, 2405, None)
    assert origin.route_count == 2


def test_a_long_excerpt_is_cut_around_the_unit(booking_trace: str, configured_db: str) -> None:
    long_text = "x" * 500 + "BK-83CE5F" + "y" * 500
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interaction_payloads SET content = %s::jsonb WHERE content_hash = 'cb-res'",
                     (json.dumps(long_text),))
        conn.execute("UPDATE payload_label_edges SET unit_start = 500, unit_end = 509")
        conn.commit()
    born = _legs(retrieval.get_leg_labels(booking_trace, _P))["request"].labels[0].carried_in[0].chains[0][0]
    ex = born.excerpt
    assert ex.text == "…" + "x" * 80 + "BK-83CE5F" + "y" * 80 + "…"
    assert ex.text[ex.start:ex.end] == "BK-83CE5F"


def test_a_unit_outside_its_payload_has_no_excerpt(booking_trace: str, configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE payload_label_edges SET unit_start = 9000, unit_end = 9009")
        conn.commit()
    born = _legs(retrieval.get_leg_labels(booking_trace, _P))["request"].labels[0].carried_in[0].chains[0][0]
    assert born.excerpt is None and born.role == "origin"


# A value born in an earlier trace. No finder writes these rows yet; the
# read must already follow them. Trace "later" has two legs: X took the value
# from trace "earlier"'s leg O (the origin), Y took it from X.
_EARLIER, _LATER = "trace-earlier", "trace-later"


@pytest.fixture()
def two_traces(configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq) "
            "VALUES ('e-a', 'agent', 'nk-a', 'agent-a', 'span-attr', 1), "
            "('e-b', 'tool', 'nk-b', 'tool-b', 'span-attr', 1)"
        )
        _interaction(conn, "ix-o", _EARLIER, "e-a", "e-b", [("response", "o-res", 5)])
        _interaction(conn, "ix-x", _LATER, "e-a", "e-b", [("request", "x-req", 12)])
        _interaction(conn, "ix-y", _LATER, "e-a", "e-b", [("request", "y-req", 19)])
        o, x, y = ("ix-o", "response", "o-res"), ("ix-x", "request", "x-req"), ("ix-y", "request", "y-req")
        ssn = dict(origin=o, unit=(3, 14), entity_type="SSN", tags=["PII"], level="RESTRICTED",
                   value="123-45-6789", origin_trace=_EARLIER)
        _edge2(conn, eid="x-from-o", trace=_LATER, to=x, frm=o, from_trace=_EARLIER, span=(0, 11), seq=12, **ssn)
        _edge2(conn, eid="y-from-x", trace=_LATER, to=y, frm=x, span=(4, 15), seq=19, **ssn)
        # Nonsense the walk must not follow: an edge INTO the origin for its own unit.
        _edge2(conn, eid="o-from-y", trace=_EARLIER, to=o, frm=y, from_trace=_LATER, span=(3, 14), seq=5,
               **{**ssn, "origin_trace": None})
        conn.execute(
            "INSERT INTO label_lineage_trace_status (trace_id, status, unclassified_legs, "
            "unanchored_findings, derived_legs, edge_count, matcher) "
            "VALUES (%s, 'complete', 0, 0, 2, 2, 'exact')", (_LATER,),
        )
        conn.commit()


def test_a_chain_crosses_into_the_origin_trace_and_stops_there(two_traces) -> None:
    req = _legs(retrieval.get_leg_labels(_LATER, "ix-y"))["request"]
    (ssn,) = req.labels
    (origin,) = ssn.carried_in
    assert origin.origin_trace_id == _EARLIER
    (chain,) = origin.chains
    assert [(h.seq, h.role, h.trace_id) for h in chain] == [
        (5, "origin", _EARLIER), (12, "hop", _LATER), (19, "here", _LATER)]
    assert (chain[0].text, chain[0].start, chain[0].end) == ("123-45-6789", 3, 14)
    assert (chain[1].start, chain[1].end) == (0, 11)


def test_the_one_hop_cross_trace_chain(two_traces) -> None:
    (origin,) = _legs(retrieval.get_leg_labels(_LATER, "ix-x"))["request"].labels[0].carried_in
    assert [(h.seq, h.trace_id) for h in origin.chains[0]] == [(5, _EARLIER), (12, _LATER)]


# Guards: edges run from earlier to later seq, so a cycle cannot exist; the walk
# must still end, and the route count must stay a count, never a listing.


def test_a_cycle_ends_the_walk_as_a_truncated_chain(configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        _interaction(conn, "ix-z", "t-cyc", "e-a", "e-b", [("request", "z", 30)])
        _interaction(conn, "ix-a", "t-cyc", "e-a", "e-b", [("request", "a", 20)])
        _interaction(conn, "ix-b", "t-cyc", "e-a", "e-b", [("request", "b", 10)])
        z, a, b = ("ix-z", "request", "z"), ("ix-a", "request", "a"), ("ix-b", "request", "b")
        o = ("ix-never", "response", "never")
        k = dict(origin=o, unit=(0, 9), entity_type="PID", tags=["PII"], level="RESTRICTED")
        _edge2(conn, eid="z-a", trace="t-cyc", to=z, frm=a, span=(0, 9), **k)
        _edge2(conn, eid="a-b", trace="t-cyc", to=a, frm=b, span=(0, 9), **k)
        _edge2(conn, eid="b-a", trace="t-cyc", to=b, frm=a, span=(0, 9), **k)
        conn.commit()
    (origin,) = _legs(retrieval.get_leg_labels("t-cyc", "ix-z"))["request"].labels[0].carried_in
    (chain,) = origin.chains
    # a → b → a(repeat, stop): the chain is kept, its first hop is not an origin.
    assert [(h.interaction_id, h.role) for h in chain] == [
        ("ix-a", "hop"), ("ix-b", "hop"), ("ix-a", "hop"), ("ix-z", "here")]


def test_converging_branches_do_not_end_a_route_that_cannot_reach_the_origin(configured_db: str) -> None:
    # z ← m, m ← b and c, b and c ← d, d ← e, e ← f; the origin is never reached.
    # The second branch meets d already walked: that is convergence, not a dead
    # end, so the route runs on to f, the edge with no edge of its own.
    with psycopg.connect(configured_db) as conn:
        names = ("f", "e", "d", "c", "b", "m", "z")
        legs = {n: (f"ix-{n}", "request", n) for n in names}
        for i, n in enumerate(names):
            _interaction(conn, legs[n][0], "t-conv", "e-a", "e-b", [("request", n, 10 + i)])
        k = dict(origin=("ix-never", "response", "never"), unit=(0, 9), entity_type="PID",
                 tags=["PII"], level="RESTRICTED")
        for to, frm in [("z", "m"), ("m", "b"), ("m", "c"), ("b", "d"), ("c", "d"), ("d", "e"), ("e", "f")]:
            _edge2(conn, eid=f"{to}-{frm}", trace="t-conv", to=legs[to], frm=legs[frm], span=(0, 9), **k)
        conn.commit()
    (origin,) = _legs(retrieval.get_leg_labels("t-conv", "ix-z"))["request"].labels[0].carried_in
    (chain,) = origin.chains
    assert [(h.interaction_id, h.role) for h in chain] == [
        ("ix-f", "hop"), ("ix-e", "hop"), ("ix-d", "hop"), ("ix-b", "hop"), ("ix-m", "hop"), ("ix-z", "here")]
    assert origin.route_count == 2
    assert {c.interaction_id for c in origin.carriers} == {f"ix-{n}" for n in names}


def test_the_walk_stops_at_the_origin_whatever_leads_into_it(configured_db: str) -> None:
    # A data fault: two edges of the unit lead INTO the origin leg itself. The
    # walk ends at the origin and counts one route, never what lies behind it.
    with psycopg.connect(configured_db) as conn:
        legs = [(f"ix-{n}", "request", n) for n in ("o", "p", "q", "z")]
        for i, (ix, lt, h) in enumerate(legs):
            _interaction(conn, ix, "t-behind", "e-a", "e-b", [(lt, h, 10 + i)])
        o, p, q, z = legs
        k = dict(origin=o, unit=(0, 9), entity_type="PID", tags=["PII"], level="RESTRICTED")
        _edge2(conn, eid="z-o", trace="t-behind", to=z, frm=o, span=(0, 9), **k)
        _edge2(conn, eid="o-p", trace="t-behind", to=o, frm=p, span=(0, 9), **k)
        _edge2(conn, eid="o-q", trace="t-behind", to=o, frm=q, span=(0, 9), **k)
        conn.commit()
    (origin,) = _legs(retrieval.get_leg_labels("t-behind", "ix-z"))["request"].labels[0].carried_in
    assert origin.route_count == 1
    assert [_route(c) for c in origin.chains] == [[(10, "origin"), (13, "here")]]


def test_routes_are_counted_not_listed_when_fan_in_multiplies_them(configured_db: str) -> None:
    # A ladder where every payload holds the value from the two before it (an
    # agent that remembers, echoed once per step): routes to rung n are the n-th
    # Fibonacci number. Enumerating them (the read before this one) is 832,040
    # walks for 30 rungs; the sub-graph is 57 edges.
    with psycopg.connect(configured_db) as conn:
        legs = [(f"ix-{i}", "request", f"h{i}") for i in range(30)]
        for i, (ix, lt, h) in enumerate(legs):
            _interaction(conn, ix, "t-fib", "e-a", "e-b", [(lt, h, 10 + i)])
        k = dict(origin=legs[0], unit=(0, 9), entity_type="PID", tags=["PII"], level="RESTRICTED")
        for i in range(1, 30):
            for back in (1, 2):
                if i - back >= 0:
                    _edge2(conn, eid=f"e{i}-{back}", trace="t-fib", to=legs[i], frm=legs[i - back],
                           span=(0, 9), **k)
        conn.commit()
    req = _legs(retrieval.get_leg_labels("t-fib", "ix-29"))["request"]
    (origin,) = req.labels[0].carried_in
    assert origin.route_count == 832040
    # One chain per edge into the leg (from ix-28 and from ix-27), each the
    # shortest route from the origin through that hop: 15 steps of two rungs.
    assert len(origin.chains) == 2
    assert [h.role for h in origin.chains[0]] == ["origin", *["hop"] * 14, "here"]
    assert origin.chains[0][0].interaction_id == "ix-0"


def _plan_rows(node: dict) -> int:
    """Rows every node of an EXPLAIN ANALYZE plan produced, over all its loops
    (``Actual Rows`` is the per-loop mean, rounded, so this is approximate)."""
    return int(node["Actual Rows"] * node["Actual Loops"]) + sum(_plan_rows(c) for c in node.get("Plans", []))


def test_the_sub_graph_read_joins_each_holding_once_not_each_pair_of_edges(configured_db: str) -> None:
    # The value reaches x n times and leaves it n times. A walk edge by edge joins
    # every edge out of x to every edge into it, n x n rows; the walk by holding
    # reads x's edges once.
    n = 300
    with psycopg.connect(configured_db) as conn:
        o, x, y = (("ix-o", "response", "o"), ("ix-x", "request", "x"), ("ix-y", "request", "y"))
        k = dict(trace="t-fan", origin=o, unit=(0, 9), entity_type="PID", tags=["PII"], level="RESTRICTED")
        for i in range(n):
            _edge2(conn, eid=f"in-{i}", to=x, frm=o, span=(10 * i, 10 * i + 9), **k)
            _edge2(conn, eid=f"out-{i}", to=y, frm=x, span=(10 * i, 10 * i + 9), **k)
        conn.commit()
    anchors = [f"out-{i}" for i in range(n)]
    with db.transaction() as tx:
        assert len(tx.fetch_all(labels._SUBGRAPH_SQL, (anchors, anchors))) == 2 * n
        (plan,) = tx.fetch_one("EXPLAIN (ANALYZE, FORMAT JSON) " + labels._SUBGRAPH_SQL, (anchors, anchors))
    work = _plan_rows(plan[0]["Plan"])
    assert work < 10 * 2 * n, work  # a walk edge by edge: about n * n (94,800 here)


def _plan_values(node: dict, key: str) -> list:
    """Every value of *key* in an EXPLAIN (FORMAT JSON) plan, depth first."""
    return [*([node[key]] if key in node else []), *(v for c in node.get("Plans", []) for v in _plan_values(c, key))]


def test_a_long_chain_among_other_traces_is_read_by_holding_through_the_to_index(configured_db: str) -> None:
    # One value carried through 2,000 hops in a table that also holds 60,000 edges
    # of other traces and units, analysed: the planner faces the real choice. The
    # origin-unit index looks like 1 row per lookup but returns all 2,000 edges of
    # the value at every hop (a product); the to-index on (leg, payload) returns
    # the one edge into each holding. Migration 0030 adds the payload to it.
    n, noise = 2_000, 60_000
    cols = ("edge_id, trace_id, to_interaction_id, to_leg_type, to_payload_hash, from_interaction_id, "
            "from_leg_type, from_payload_hash, origin_interaction_id, origin_leg_type, origin_payload_hash, "
            "unit_start, unit_end, unit_text, unit_value, entity_type, regulatory_tags, sensitivity_level, "
            "matcher, match_kind, score, held_from, held_to, seq")

    def edge(eid, trace, to, frm, origin, start):
        return [eid, trace, to, "request", to, frm, "request", frm, origin, "request", origin,
                start, start + 9, "v", "v", "PID", "{PII}", "RESTRICTED", "exact", "whole", 1, 0, 9, 1]

    with psycopg.connect(configured_db) as conn:
        with conn.cursor() as cur, cur.copy(f"COPY payload_label_edges ({cols}) FROM STDIN") as copy:
            for i in range(noise):  # 30 traces, 600 values of 100 edges, 20 edges into each leg
                copy.write_row(edge(f"nz{i}", f"t-nz{i // 2000}", f"nz{i // 20}", f"nz{i // 20 - 1}",
                                    f"nzo{i // 100}", i % 20))
            for i in range(1, n + 1):
                copy.write_row(edge(f"c{i}", "t-chain", f"c{i}", f"c{i - 1}", "c0", 0))
        conn.commit()
        conn.execute("ANALYZE payload_label_edges")
    anchors = [f"c{n}"]
    with db.transaction() as tx:
        assert len(tx.fetch_all(labels._SUBGRAPH_SQL, (anchors, anchors))) == n
        (plan,) = tx.fetch_one("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + labels._SUBGRAPH_SQL, (anchors, anchors))
    root = plan[0]["Plan"]
    indexes = set(_plan_values(root, "Index Name"))
    blocks = root["Shared Hit Blocks"] + root["Shared Read Blocks"]
    # A few buffer blocks per hop (6 here); through the origin-unit index, a block
    # count that grows with the chain at every hop.
    assert blocks < 30 * n, (blocks, indexes)
    assert "payload_label_edges_to_idx" in indexes and "payload_label_edges_origin_idx" not in indexes, indexes
