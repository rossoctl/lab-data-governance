"""``retrieval.labels.merge`` — found here vs carried in, per (span, entity_type).

Pure: no DB. The reference case is a leg from a live run (trace
e06cd67d…, interaction f48567d1…, the notify-partner request, seq 92738): the
classifier's three findings on ``{"booking_id": "BK-2850EB", "dates": "2035-11-08
to 2035-11-13"}`` and the five edges label lineage wrote into it, values as read.
"""

from __future__ import annotations

from data_governance.retrieval.labels import LabelEdge, _carried_summary, merge

_LLM = ("c0f9059b-c66e-5fde-b19b-32537fe8a8f8", "response", "e01c0d85", 91707,
        "booking-agent", "host.containers.internal:11434/qwen2.5:7b")
_BOOKING = ("efc92832-1b3b-581c-9aab-431ff95518fd", "response", "4a0b9fb6", 90574,
            "booking-agent", "create-booking")
_USER = ("76099bc3-93c4-53e9-b2ac-58e91e543630", "request", "7cc5557f", 87834,
         "demo-client", "travel-advisor")
_FLIGHTS = ("50f2e157-5d46-5b91-8f86-76d4f9a66e53", "response", "cb7cb3d3", 87854,
            "travel-advisor", "get-flights")


def _finding(entity_type: str, start: int, end: int, text: str, level: str = "INTERNAL",
             tags: tuple[str, ...] = ("PI",)) -> dict:
    return {"entity_type": entity_type, "start": start, "end": end, "text": text,
            "sensitivity_level": level, "regulatory_tags": list(tags)}


def _edge(origin: tuple, entity_type: str, span: tuple[int, int] | None, unit: tuple[int, int],
          unit_text: str, unit_value: str, *, matched_text: str | None = None,
          level: str = "INTERNAL", tags: tuple[str, ...] = ("PI",)) -> LabelEdge:
    iid, leg, phash, seq, caller, callee = origin
    return LabelEdge(
        entity_type=entity_type, regulatory_tags=tags, sensitivity_level=level,
        matched_text=(matched_text or unit_value) if span else None,
        matched_start=span[0] if span else None, matched_end=span[1] if span else None,
        origin_interaction_id=iid, origin_leg_type=leg, origin_payload_hash=phash,
        unit_start=unit[0], unit_end=unit[1], unit_text=unit_text, unit_value=unit_value,
        match_kind="whole", origin_seq=seq, caller=caller, callee=callee,
    )


FINDINGS = [
    _finding("OPID", 16, 25, "BK-2850EB"),
    _finding("DATE", 38, 48, "2035-11-08"),
    _finding("DATE", 52, 62, "2035-11-13"),
]
EDGES = [
    _edge(_LLM, "OPID", (16, 25), (7, 16), "BK-2850EB", "BK-2850EB"),
    _edge(_BOOKING, "PID", (16, 25), (107, 116), "BK-2850EB", "BK-2850EB",
          level="RESTRICTED", tags=("PII",)),
    _edge(_USER, "DATE", (38, 48), (79, 89), "2035-11-08", "2035-11-08"),
    _edge(_FLIGHTS, "DATE", (38, 48), (57, 67), "2035", "2035-11-08"),
    _edge(_USER, "DATE", (52, 62), (93, 103), "2035-11-13", "2035-11-13"),
]


def _shape(rows) -> list[tuple]:
    return [(r.entity_type, r.start, r.end, r.found_here, [o.origin_seq for o in r.carried_in])
            for r in rows]


def test_reference_leg_gives_four_labels() -> None:
    rows = merge(FINDINGS, EDGES)
    assert _shape(rows) == [
        # Same span, two types: the carried-only PID sorts above the OPID (higher level).
        ("PID", 16, 25, False, [90574]),
        ("OPID", 16, 25, True, [91707]),
        ("DATE", 38, 48, True, [87834, 87854]),
        ("DATE", 52, 62, True, [87834]),
    ]
    pid = rows[0]
    assert (pid.text, pid.sensitivity_level, pid.regulatory_tags) == ("BK-2850EB", "RESTRICTED", ["PII"])
    assert (pid.carried_in[0].caller, pid.carried_in[0].callee) == ("booking-agent", "create-booking")
    # What the classifier marked at get-flights is kept beside the value it denotes.
    year = rows[2].carried_in[1]
    assert (year.unit_text, year.unit_value, year.match_kind) == ("2035", "2035-11-08", "whole")


def test_finding_without_edge_is_found_here_only() -> None:
    rows = merge([_finding("EMAIL", 0, 14, "jo@example.com")], [])
    assert _shape(rows) == [("EMAIL", 0, 14, True, [])]


def test_no_findings_gives_carried_in_rows_only() -> None:
    # An unclassified payload: what lineage carried is still listed.
    rows = merge([], EDGES[:2])
    assert [(r.entity_type, r.found_here) for r in rows] == [("PID", False), ("OPID", False)]


def test_a_narrower_numeric_finding_pairs_by_overlap() -> None:
    # The classifier marked the year here; the edge holds the whole date.
    rows = merge([_finding("DATE", 38, 42, "2035")], [EDGES[2]])
    assert _shape(rows) == [("DATE", 38, 42, True, [87834])]
    assert rows[0].text == "2035"


def test_overlap_of_another_type_does_not_pair() -> None:
    rows = merge([_finding("OPID", 16, 25, "BK-2850EB")], [EDGES[1]])
    assert _shape(rows) == [("PID", 16, 25, False, [90574]), ("OPID", 16, 25, True, [])]


def test_an_edge_without_a_span_is_a_row_without_a_span() -> None:
    spanless = _edge(_USER, "PERSON", None, (0, 4), "Dana", "Dana")
    rows = merge(FINDINGS[:1], [spanless])
    assert _shape(rows) == [("OPID", 16, 25, True, []), ("PERSON", None, None, False, [87834])]
    assert rows[1].text == "Dana"


def test_two_overlapping_same_type_labels_stay_two_rows() -> None:
    findings = [_finding("DATE", 38, 48, "2035-11-08"), _finding("DATE", 44, 54, "11-08 to 2")]
    rows = merge(findings, [EDGES[2]])
    assert _shape(rows) == [("DATE", 38, 48, True, [87834]), ("DATE", 44, 54, True, [])]


def test_two_carriers_of_one_origin_are_one_origin() -> None:
    rows = merge(FINDINGS[:1], [EDGES[0], EDGES[0]])
    assert len(rows[0].carried_in) == 1


def test_carried_level_raises_the_row_level() -> None:
    rows = merge([_finding("OPID", 16, 25, "BK-2850EB")],
                 [_edge(_LLM, "OPID", (16, 25), (7, 16), "BK-2850EB", "BK-2850EB",
                        level="CONFIDENTIAL", tags=("PCI",))])
    assert (rows[0].sensitivity_level, rows[0].regulatory_tags) == ("CONFIDENTIAL", ["PCI", "PI"])


def test_carried_origin_count_counts_origin_legs_not_edges() -> None:
    # Five edges from four origin legs: the user request (87834) carries both dates.
    assert _carried_summary(EDGES).origin_count == 4


def test_a_finding_without_offsets_is_a_row_without_a_span_and_claims_nothing() -> None:
    no_offsets = {"entity_type": "OPID", "text": "BK-2850EB", "sensitivity_level": "INTERNAL",
                  "regulatory_tags": ["PI"]}
    rows = merge([no_offsets], [EDGES[0]])
    assert _shape(rows) == [("OPID", 16, 25, False, [91707]), ("OPID", None, None, True, [])]
