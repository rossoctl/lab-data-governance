"""#348's two pictures. A tool or an LLM keeps one memory per call it receives: what
it sends inside that call inherits from the request that opened the call and from
the responses it received inside the call before it. Every leg carries one value, so
the edges the fold draws are the routing; an edge is (source, destination), legs
numbered by time."""

from __future__ import annotations

import datetime as dt

import pytest

from data_governance.label_lineage import find, fold
from data_governance.processors.data_lineage.traversal import Entity, inbound_sets

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


def _entities(c_kind: str) -> dict[str, Entity]:
    kinds = {"A": "agent", "B": "agent", "C": c_kind, "D": "tool", "E": "tool", "F": "tool", "X": "tool"}
    return {e: Entity(id=e, natural_key=f"{k}:lab/{e}", kind=k) for e, k in kinds.items()}


def _legs(picture: dict, seconds: dict[int, float] | None = None) -> list[fold.WireLeg]:
    seconds = seconds or {}
    return [fold.WireLeg(ix, lt, n, caller, callee, f"h{n}", occurred_at=T0 + dt.timedelta(seconds=seconds.get(n, n)),
                         parent_interaction_id=parent)
            for n, (ix, lt, caller, callee, parent) in picture.items()]


def edges(picture: dict, c_kind: str, seconds: dict[int, float] | None = None,
          declared: dict[str, str] | None = None) -> set[tuple[int, int]]:
    """The (source, destination) edges the fold draws over *picture*, C being of *c_kind*;
    leg *n* is observed at *seconds[n]* (default *n*) after T0; *declared* is the memory
    scope declaration (natural key -> scope)."""
    payloads = {f"h{n}": {"mrn": MRN, "n": n} for n in picture}
    findings = {f"h{n}": [FINDING] for n in picture}
    trace = fold.TraceInput("t348", _legs(picture, seconds), _entities(c_kind), payloads, findings)
    d = fold.derive(trace, find.exact, **({"declared": declared} if declared is not None else {}))
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
        6: ("ix", "response", "B", "X", None),  # X never received D's answer
    }
    assert edges(picture, c_kind) == {(1, 3), (3, 4), (1, 5), (2, 6)}


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


# --- the memory scope declared per entity ---------------------------------------

AGENT_EDGES = {(1, 3), (2, 3), (3, 4), (1, 5), (2, 5), (4, 5), (5, 6),
               (1, 7), (2, 7), (4, 7), (6, 7), (1, 8), (2, 8), (4, 8), (6, 8)}
PER_CALL_EDGES = {(1, 3), (3, 4), (1, 7), (4, 7), (2, 5), (5, 6), (2, 8), (6, 8)}


def test_a_tool_declared_conversation_mixes_its_calls() -> None:
    assert edges(TWO_CALLERS, "tool") == PER_CALL_EDGES
    assert edges(TWO_CALLERS, "tool", declared={"tool:lab/C": "conversation"}) == AGENT_EDGES


def test_an_agent_declared_call_follows_the_per_call_rule() -> None:
    assert edges(TWO_CALLERS, "agent") == AGENT_EDGES
    assert edges(TWO_CALLERS, "agent", declared={"agent:lab/C": "call"}) == PER_CALL_EDGES


@pytest.mark.parametrize(("c_kind", "want"), [("tool", PER_CALL_EDGES), ("llm", PER_CALL_EDGES),
                                              ("service", PER_CALL_EDGES), ("agent", AGENT_EDGES)])
def test_persistent_keeps_what_the_kind_does_inside_one_conversation(c_kind: str, want: set) -> None:
    """Persistent adds memory across conversations; inside one the entity does what its kind does."""
    assert edges(TWO_CALLERS, c_kind, declared={f"{c_kind}:lab/C": "persistent"}) == want


def test_a_declaration_for_another_entity_changes_nothing() -> None:
    assert edges(TWO_CALLERS, "tool", declared={"tool:lab/elsewhere": "conversation"}) == PER_CALL_EDGES


# with_invocation_scope's result on TWO_CALLERS before the scope existed (main e93d1f83), leg -> inbound legs
_MAIN_PER_CALL = {1: (), 2: (), 3: (1,), 4: (3,), 5: (2,), 6: (5,), 7: (1, 4), 8: (2, 6)}
_MAIN_AGENT = {1: (), 2: (), 3: (1, 2), 4: (3,), 5: (1, 2, 4), 6: (5,), 7: (1, 2, 4, 6), 8: (1, 2, 4, 6)}


@pytest.mark.parametrize("c_kind", ["tool", "llm", "service", "user", "client", "agent"])
def test_with_no_declaration_invocation_scope_is_what_it_was(c_kind: str) -> None:
    ordered = fold.wire_order(_legs(TWO_CALLERS))
    entities = _entities(c_kind)
    inbound = inbound_sets(ordered, entities, pair_responses=True)
    number = {(ix, lt): n for n, (ix, lt, *_rest) in TWO_CALLERS.items()}
    out = fold.with_invocation_scope(ordered, inbound, entities)
    assert {number[k]: tuple(number[x] for x in v) for k, v in out.items()} == (
        _MAIN_AGENT if c_kind == "agent" else _MAIN_PER_CALL)


# One conversation with all four kinds that matter: an agent calling an LLM twice and a tool twice, the tool calling
# a service in each of its calls (leg -> (interaction, leg type, caller, callee, parent interaction)).
FOUR_KINDS = {
    1: ("iu", "request", "U", "A", None),
    2: ("il1", "request", "A", "L", "iu"),
    3: ("il1", "response", "A", "L", "iu"),
    4: ("it1", "request", "A", "T", "iu"),
    5: ("is1", "request", "T", "S", "it1"),
    6: ("is1", "response", "T", "S", "it1"),
    7: ("it1", "response", "A", "T", "iu"),
    8: ("it2", "request", "A", "T", "iu"),
    9: ("is2", "request", "T", "S", "it2"),
    10: ("is2", "response", "T", "S", "it2"),
    11: ("it2", "response", "A", "T", "iu"),
    12: ("il2", "request", "A", "L", "iu"),
    13: ("il2", "response", "A", "L", "iu"),
    14: ("iu", "response", "U", "A", None),
}
# fold.derive's edges over FOUR_KINDS before the scope existed (main e93d1f83): the agent pools, the LLM, the tool and
# the service keep one memory per call.
_MAIN_FOUR_KINDS_EDGES = {
    (1, 2), (1, 4), (1, 8), (1, 12), (1, 14), (2, 3), (3, 4), (3, 8), (3, 12), (3, 14), (4, 5), (4, 7), (5, 6),
    (6, 7), (7, 8), (7, 12), (7, 14), (8, 9), (8, 11), (9, 10), (10, 11), (11, 12), (11, 14), (12, 13), (13, 14)}


def test_with_no_declaration_derive_is_what_it_was_on_a_tool_an_llm_an_agent_and_a_service() -> None:
    kinds = {"U": "user", "A": "agent", "L": "llm", "T": "tool", "S": "service"}
    entities = {e: Entity(id=e, natural_key=f"{k}:lab/{e}", kind=k) for e, k in kinds.items()}
    payloads = {f"h{n}": {"mrn": MRN, "n": n} for n in FOUR_KINDS}
    findings = {f"h{n}": [FINDING] for n in FOUR_KINDS}
    d = fold.derive(fold.TraceInput("t4", _legs(FOUR_KINDS), entities, payloads, findings), find.exact)
    number = {(ix, lt): n for n, (ix, lt, *_rest) in FOUR_KINDS.items()}
    assert {(number[e.from_key], number[e.to_key]) for e in d.edges} == _MAIN_FOUR_KINDS_EDGES
    assert len(d.edges) == len(_MAIN_FOUR_KINDS_EDGES)
