"""#348's two pictures. A tool or an LLM keeps one memory per call it receives: what
it sends inside that call inherits from the request that opened the call and from
the responses it received inside the call before it. Every leg carries one value, so
the edges the fold draws are the routing; an edge is (source, destination), legs
numbered by time."""

from __future__ import annotations

import datetime as dt

import pytest

from data_governance.label_lineage import find, fold
from data_governance.processors.data_lineage.traversal import Entity

T0 = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.timezone.utc)
MRN = "4471820"
FINDING = {"entity_type": "MRN", "text": MRN, "start": 9, "end": 16,
           "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED"}

# number -> (interaction, leg type, caller, callee, parent interaction)
ONE_CALLER = {  # A calls C; C calls D, then F, then answers A
    1: ("ia", "request", "A", "C", None),
    2: ("id", "request", "C", "D", "ia"),
    3: ("id", "response", "C", "D", "ia"),
    4: ("if", "request", "C", "F", "ia"),
    5: ("if", "response", "C", "F", "ia"),
    6: ("ia", "response", "A", "C", None),
}
TWO_CALLERS = {  # A and B call C; C calls D for A, E for B; 2 reaches C before 3
    1: ("ia", "request", "A", "C", None),
    2: ("ib", "request", "B", "C", None),
    3: ("id", "request", "C", "D", "ia"),
    4: ("id", "response", "C", "D", "ia"),
    5: ("ie", "request", "C", "E", "ib"),
    6: ("ie", "response", "C", "E", "ib"),
    7: ("ia", "response", "A", "C", None),
    8: ("ib", "response", "B", "C", None),
}


def edges(picture: dict, c_kind: str, seconds: dict[int, float] | None = None) -> set[tuple[int, int]]:
    """The (source, destination) edges the fold draws over *picture*, C being of *c_kind*;
    leg *n* is observed at *seconds[n]* (default *n*) after T0."""
    seconds = seconds or {}
    kinds = {"A": "agent", "B": "agent", "C": c_kind, "D": "tool", "E": "tool", "F": "tool", "X": "tool"}
    entities = {e: Entity(id=e, natural_key=f"{k}:lab/{e}", kind=k) for e, k in kinds.items()}
    legs = [fold.WireLeg(ix, lt, n, caller, callee, f"h{n}", occurred_at=T0 + dt.timedelta(seconds=seconds.get(n, n)),
                         parent_interaction_id=parent)
            for n, (ix, lt, caller, callee, parent) in picture.items()]
    payloads = {f"h{n}": {"mrn": MRN, "n": n} for n in picture}
    findings = {f"h{n}": [FINDING] for n in picture}
    d = fold.derive(fold.TraceInput("t348", legs, entities, payloads, findings), find.exact)
    number = {(ix, lt): n for n, (ix, lt, *_rest) in picture.items()}
    return {(number[e.from_key], number[e.to_key]) for e in d.edges}


@pytest.mark.parametrize("c_kind", ["tool", "llm"])
def test_one_caller(c_kind: str) -> None:
    assert edges(ONE_CALLER, c_kind) == {(1, 2), (2, 3), (1, 4), (3, 4), (4, 5), (1, 6), (3, 6), (5, 6)}


@pytest.mark.parametrize("c_kind", ["tool", "llm"])
def test_two_callers(c_kind: str) -> None:
    assert edges(TWO_CALLERS, c_kind) == {(1, 3), (3, 4), (1, 7), (4, 7), (2, 5), (5, 6), (2, 8), (6, 8)}


@pytest.mark.parametrize("c_kind", ["tool", "llm"])
def test_a_request_whose_call_is_absent_keeps_the_latest_leg(c_kind: str) -> None:
    picture = {
        1: ("ia", "request", "A", "C", None),
        2: ("id", "request", "C", "D", "iz"),  # iz: no leg of it in the trace
        3: ("id", "response", "C", "D", "iz"),
        4: ("ia", "response", "A", "C", None),
    }
    assert edges(picture, c_kind) == {(1, 2), (2, 3), (1, 4)}


@pytest.mark.parametrize("c_kind", ["tool", "llm"])
def test_a_request_whose_call_another_entity_received_keeps_the_latest_leg(c_kind: str) -> None:
    picture = {
        1: ("ia", "request", "A", "C", None),
        2: ("ix", "request", "B", "X", None),
        3: ("id", "request", "C", "D", "ix"),  # names X's call, not one C received
        4: ("id", "response", "C", "D", "ix"),
        5: ("ia", "response", "A", "C", None),
    }
    assert edges(picture, c_kind) == {(1, 3), (3, 4), (1, 5)}


@pytest.mark.parametrize("c_kind", ["tool", "llm"])
def test_a_response_after_the_answer_does_not_reach_it(c_kind: str) -> None:
    """C answers A before D answers C (an asynchronous child, well past the skew
    tolerance): D's answer cannot be in C's."""
    picture = {
        1: ("ia", "request", "A", "C", None),
        2: ("id", "request", "C", "D", "ia"),
        3: ("ia", "response", "A", "C", None),
        4: ("id", "response", "C", "D", "ia"),
    }
    assert edges(picture, c_kind, seconds={4: 10}) == {(1, 2), (1, 3), (2, 4)}


def test_an_agent_keeps_one_memory_for_all_calls() -> None:
    """#349: an agent's memory is not per call, so the other caller's request reaches it."""
    assert edges(TWO_CALLERS, "agent") == {
        (1, 3), (2, 3), (3, 4), (1, 5), (2, 5), (4, 5), (5, 6),
        (1, 7), (2, 7), (4, 7), (6, 7), (1, 8), (2, 8), (4, 8), (6, 8)}
