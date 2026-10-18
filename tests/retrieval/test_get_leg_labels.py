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

from data_governance import retrieval

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
