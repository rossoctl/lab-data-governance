"""The judge, the loader and the renderer without containers: every rule that refuses to guess, and the branches
no shipped case exercises."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from . import caseline as cl
from .cases import CASES_DIR, Case, Excuse, load_case
from .classify import check_authored
from .engine import LegRows, Observation, neighbourhood, resolve_edges, verdict_at
from .grid import render_grid
from .render import render
from .report import judge

LINE = "U>A[s1 -], A>T1[s1 -], T1>V1[s1 -]⚑"
EDGES = {("s1", 1, 2), ("s1", 2, 3)}
MRN = "4471820"   # s1's bytes as an MRN


def _case(**over) -> Case:
    base = dict(id="J", title="", cell="", line_text=LINE, line=cl.parse(LINE), units={"s1": "MRN"},
                verdict={"on": "block", "off": "allow"}, edges=set(EDGES), cannot=None,
                pending=None, must_see={}, must_miss={}, authored=False, notes="", dir=Path("."))
    base.update(over)
    base.setdefault("accepts", {u: frozenset({t}) for u, t in base["units"].items()})
    return Case(**base)


def _obs(mode: str, edges: set, verdict: str | None, legs: dict | None = None, problems=()) -> Observation:
    return Observation(mode=mode, legs=legs or {}, edges=edges, raw_edges=[], verdict=verdict, timings={},
                       neighbourhood={}, opa_calls=0, problems=list(problems))


def _at(index: int, *findings: tuple[str, str]) -> LegRows:
    return LegRows(leg_index=index, interaction_id=None, leg_type="request", payload_hash=None,
                   classification={"sensitivity": "RESTRICTED", "findings": list(findings)}, inherited=[], risk=None)


def _judge(case: Case, on_edges=EDGES, on="block", off="allow", legs=None, problems=(), off_edges=None):
    return judge(case, _obs("on", set(on_edges), on, legs, problems),
                 _obs("off", set(on_edges if off_edges is None else off_edges), off, legs))


def _yaml(tmp_path: Path, body: str, line: str = LINE) -> Path:
    (tmp_path / "case.yaml").write_text(f"id: Y\nline: \"{line}\"\nunits: {{s1: MRN}}\n" + body)
    return tmp_path


# --- the judge: exact comparison --------------------------------------------------------------------------------

def test_green_when_edges_and_verdicts_match():
    out = _judge(_case())
    assert out.status == "GREEN" and not out.reasons


def test_an_extra_edge_is_red():
    out = _judge(_case(), on_edges=EDGES | {("s1", 1, 3)})
    assert out.status == "RED" and "edges extra: s1 1>3" in out.reasons


@pytest.mark.parametrize("extra", [("s1", 3, 7), ("s1", 3, 10), ("s1", 1, 4)])
def test_a_cross_call_edge_is_red_even_when_an_expected_edge_reaches_the_same_leg(extra):
    # P3: leg 3 is A1's call on T1, leg 7 T1's request inside A2's call; nothing crosses calls (#348).
    p3 = load_case(CASES_DIR / "P3")
    out = _judge(p3, on_edges=p3.edges | {extra}, legs=_p3_legs(p3))
    assert out.status == "RED" and out.edge_diff[1] == {extra}


def _p3_legs(case: Case) -> dict[int, LegRows]:
    value = case.values["s1"]
    legs = {i: _at(i, ("SSN", value)) for i in case.must_see}
    legs.update({i: _at(i) for i in case.must_miss})
    return legs


@pytest.mark.parametrize("on, off, reason", [
    ("allow", "allow", "verdict on: want block, got allow"),
    ("block", "block", "verdict off: want allow, got block"),
    ("warn", "allow", "verdict on: want block, got warn"),   # compared as the record writes it
])
def test_a_wrong_verdict_is_red_in_either_mode(on, off, reason):
    out = _judge(_case(), on=on, off=off)
    assert out.status == "RED" and reason in out.reasons


def test_a_line_with_no_flagged_leg_is_judged_on_its_edges_alone():
    line = "U>A[s1 -], A>T1[s1 -]"
    case = _case(line_text=line, line=cl.parse(line), verdict={}, edges={("s1", 1, 2)})
    assert _judge(case, on_edges={("s1", 1, 2)}, on=None, off=None).status == "GREEN"
    assert _judge(case, on_edges=set(), on=None, off=None).status == "RED"


def test_on_and_off_runs_that_draw_different_edges_are_red():
    out = _judge(_case(), off_edges={("s1", 1, 2)})
    assert out.status == "RED" and out.reasons[0].startswith("the on and off runs drew different edges")


def test_a_problem_reading_the_rows_is_red_even_for_a_pending_case():
    case = _case(pending=Excuse("waits", missing=frozenset({("s1", 2, 3)}), verdict=frozenset({"on"})))
    out = _judge(case, on_edges={("s1", 1, 2)}, on="allow", off_edges={("s1", 1, 2)},
                 problems=["edge s1 1>None (MRN) has an end on no line leg"])
    assert out.status == "RED" and out.reasons[0] == "on: edge s1 1>None (MRN) has an end on no line leg"


# --- pending and cannot excuse exactly what they state ----------------------------------------------------------

WAITS = Excuse("waits", missing=frozenset({("s1", 2, 3)}), verdict=frozenset({"on"}))


def test_a_pending_case_that_fails_exactly_as_stated_is_pending():
    out = _judge(_case(pending=WAITS), on_edges={("s1", 1, 2)}, on="allow")
    assert out.status == "PENDING" and out.reasons[0] == "pending: waits"


def test_a_pending_case_that_fails_otherwise_is_red():
    out = _judge(_case(pending=WAITS), on_edges=set(), on="allow")   # 1>2 is missing too, which pending does not state
    assert out.status == "RED" and out.reasons[0].startswith("differs from what pending states")


def test_a_pending_case_that_passes_is_red():
    out = _judge(_case(pending=WAITS))
    assert out.status == "RED" and out.reasons[0].startswith("passes while marked pending")


def test_a_cannot_case_that_fails_exactly_as_stated_is_red_by_design():
    case = _case(cannot=Excuse("the value leaves through a hop no sidecar sees", missing=frozenset(EDGES),
                               verdict=frozenset({"on"})))
    out = _judge(case, on_edges=set(), on="allow")
    assert out.status == "RED-BY-DESIGN" and out.reasons[0].startswith("cannot: ")


def test_pending_must_state_what_it_excuses(tmp_path: Path):
    with pytest.raises(ValueError, match="states reason and exactly what it gets wrong"):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block, 'off': allow}\n  edges: []\n  pending: waits\n"
                                  "classifier:\n  must_see: {1: [s1], 2: [s1], 3: [s1]}\n"))


# --- the classifier premise -------------------------------------------------------------------------------------

def test_a_unit_counts_as_labelled_under_any_of_its_types_everywhere():
    case = _case(units={"s1": "NAME"}, accepts={"s1": frozenset({"NAME", "PN"})},
                 must_see={1: ["s1"], 2: ["s1"]}, must_miss={3: ["s1"]})
    name = case.values["s1"]
    legs = {1: _at(1, ("PN", name)), 2: _at(2, ("NAME", name)), 3: _at(3, ("PN", name))}
    out = _judge(case, legs=legs)
    assert out.status == "RED" and out.classifier_notes == [
        "leg 3: the model DID label s1 (NAME|PN); the case assumed it would not"]   # must_see at 1 and 2 met
    observed = render_grid(case, _obs("on", EDGES, "block", legs), None).split("observed\n", 1)[1]
    assert observed.splitlines()[2].rstrip().endswith("●")   # the grid labels leg 3 by the same rule


def test_a_type_the_unit_does_not_list_does_not_count():
    case = _case(units={"s1": "NAME"}, must_see={1: ["s1"]})
    out = _judge(case, legs={1: _at(1, ("PN", case.values["s1"]))})
    assert out.status == "RED" and out.classifier_notes == [
        "leg 1: the model did NOT label s1 (NAME); found [('PN', 'Rosa Alvarez')]"]


def test_a_finding_on_other_bytes_does_not_label_the_unit():
    case = _case(must_see={1: ["s1"]}, must_miss={2: ["s1"]})
    legs = {1: _at(1, ("MRN", "9999999")), 2: _at(2, ("MRN", "9999999"))}   # right type, another value
    out = _judge(case, legs=legs)
    assert out.classifier_notes == ["leg 1: the model did NOT label s1 (MRN); found [('MRN', '9999999')]"]


def test_a_stated_leg_with_no_classification_row_is_red():
    out = _judge(_case(must_miss={2: ["s1"]}), legs={})
    assert out.status == "RED" and out.classifier_notes == [
        "leg 2: no classification row; the case states s1 there"]


def test_the_expected_grid_draws_must_see_full_and_must_miss_hollow():
    case = _case(must_see={1: ["s1"], 2: ["s1"]}, must_miss={3: ["s1"]})
    expected = render_grid(case, None, None).split("\n\nobserved")[0]
    assert [ch for ch in expected.splitlines()[3] if ch in "●○"] == ["●", "●", "○"]


@pytest.mark.parametrize("see, miss, error", [
    ("{1: [s1], 2: [s1], 3: [s1]}", "{3: [s1]}", "in both must_see and must_miss: s1@3"),
    ("{1: [s1], 2: [s1]}", "{}", "in neither must_see nor must_miss: s1@3"),
    ("{1: [s1], 2: [s1], 3: [s1]}", "{4: [s1]}", "a unit the leg does not carry: s1@4"),
])
def test_every_present_unit_is_stated_exactly_once(tmp_path: Path, see, miss, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block, 'off': allow}\n  edges: []\n"
                                  f"classifier:\n  must_see: {see}\n  must_miss: {miss}\n"))


def test_a_unit_may_list_several_types(tmp_path: Path):
    (tmp_path / "case.yaml").write_text(
        f"id: Y\nline: \"{LINE}\"\nunits: {{s1: [NAME, PN]}}\nexpect:\n  verdict: {{'on': block, 'off': allow}}\n"
        f"  edges: []\nclassifier:\n  must_see: {{1: [s1], 2: [s1], 3: [s1]}}\n")
    case = load_case(tmp_path)
    assert case.units == {"s1": "NAME"} and case.accepts == {"s1": frozenset({"NAME", "PN"})}


def test_a_stale_authored_record_fails_loudly():
    case = _case(authored=True, must_see={1: ["s1"], 2: ["s1"]}, must_miss={3: ["s1"]})
    renderer = render(case.id, case.units, case.line)
    text = {r.leg.index: r for r in renderer.rendered}
    from data_governance.processors.classification import projection
    t3 = projection.project(text[3].content)
    check_authored(case, renderer, {t3: {"values": [MRN], "removed": 1}})        # as calibrated: fine
    with pytest.raises(ValueError, match="calibrated for another must_miss"):
        check_authored(case, renderer, {})                                         # must_miss added after calibration
    with pytest.raises(ValueError, match="calibrated for another must_miss"):
        check_authored(_case(), renderer, {t3: {"values": [MRN], "removed": 1}})  # authored dropped since


# --- what the system produced is read, never guessed -------------------------------------------------------------

def test_an_edge_with_an_end_on_no_line_leg_is_a_problem():
    case = _case()
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    resolved, _, _, problems = resolve_edges(case, [("i1", "request", "i9", "response", "MRN", MRN, MRN, "exact")],
                                             leg_of_key)
    assert resolved == set() and problems == ["edge s1 1>None (MRN) has an end on no line leg"]


def test_an_edge_carrying_another_type_is_a_problem():
    case = _case()
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    resolved, _, _, problems = resolve_edges(case, [("i1", "request", "i2", "request", "SSN", MRN, MRN, "exact")],
                                             leg_of_key)
    assert resolved == {("s1", 1, 2)} and problems == ["edge s1 1>2 carries SSN, not one of ['MRN']"]


def test_an_edge_on_bytes_of_no_unit_is_an_extra():
    case = _case()
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    resolved, _, _, _ = resolve_edges(case, [("i1", "request", "i2", "request", "MRN", "123", "123", "exact")],
                                      leg_of_key)
    assert resolved == {("?123", 1, 2)}


def _record(version, legs, enforcement):
    return {"version": version, "legs": legs, "enforcement_type": enforcement}


@pytest.mark.parametrize("records, role, verdict, problem", [
    ([_record(1, ["request"], "block")], "request", "block", None),
    ([_record(1, ["request"], "allow"), _record(2, ["request", "response"], "allow")], "request", "allow", None),
    ([_record(1, ["response"], "block")], "request", None, "no risk record evidences the flagged request leg"),
    ([_record(1, ["request"], "allow"), _record(2, ["request", "response"], "block")], "request", None,
     "risk records disagree at the flagged leg: v1 allow, v2 block"),
])
def test_the_verdict_is_the_record_that_evidences_the_flagged_leg(records, role, verdict, problem):
    record, got = verdict_at(records, role)
    assert (record["enforcement_type"] if record else None, got) == (verdict, problem)


def test_neighbourhood_follows_every_route_back():
    # s1 reaches 4 from two origins: 1 (born beside s2) and 3 (born alone). Both count, neither is chosen.
    line = "U>A[s1 s2 -], A>L[-], L>A[s1 -], A>T1[s1 s2 -]"
    case = _case(line_text=line, line=cl.parse(line), units={"s1": "MRN", "s2": "SSN"}, verdict={})
    edges = {("s1", 1, 4), ("s1", 3, 4), ("s2", 1, 4)}
    assert neighbourhood(case, edges)[4] == {"s1": "1 of 1", "s2": "1 of 1"}


# --- the case states everything; the renderer refuses an ambiguous line ------------------------------------------

def test_edges_are_required(tmp_path: Path):
    with pytest.raises(ValueError, match="expect.edges is required"):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block, 'off': allow}\n"))


def test_an_unknown_expect_key_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match=re.escape("unknown expect keys ['also']")):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block, 'off': allow}\n  edges: []\n  also: []\n"))


def test_two_units_never_travel_as_the_same_bytes():
    with pytest.raises(ValueError, match="would travel as the same bytes 'Rosa Alvarez'"):
        render("Y", {"s1": "NAME", "s2": "PN"}, cl.parse("U>A[s1 s2@who -]"))


@pytest.mark.parametrize("line, error", [
    ("U>A[s1 s1 -]", "two values under the key 'mrn'"),
    ("U>A[s1@id x1@id -]", "two values under the key 'id'"),
    ("U>A[x1@note -]", "key 'note' is reserved for filler"),
])
def test_a_payload_never_loses_a_value(line, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        render("Y", {"s1": "MRN"}, cl.parse(line))


@pytest.mark.parametrize("line, error", [
    ("U>A[-], T1>A[s1 -]", "T1>A answers no open request"),
    ("U>A1[-], U>A2[-], A1>T1[-], A2>T1[-], T1>V1[s1 -]", "T1 is serving 2 calls (A1, A2)"),
    ("U>A[-], A>T1[-], A>T1[-], T1>A[s1 -]", "T1>A could answer 2 open calls A>T1"),
])
def test_an_ambiguous_line_is_refused(line, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        render("Y", {"s1": "MRN"}, cl.parse(line))


def test_a_named_call_resolves_the_ambiguity():
    r = render("Y", {"s1": "MRN"}, cl.parse("U>A1[-], U>A2[-], A1>T1[-], A2>T1[-], T1(A1)>V1[s1 -]"))
    assert [x.leg.index for x in r.rendered] == [1, 2, 3, 4, 5]


# --- the report, the line --------------------------------------------------------------------------------------

def test_the_report_draws_the_expected_grid_above_the_observed():
    grid = render_grid(_case(), _obs("on", set(), "allow"), _obs("off", set(), "allow"))
    expected, observed = grid.split("\n\nobserved\n")
    assert expected.startswith("expected\n")
    assert "━" in expected and "━" not in observed.split("verdict")[0]
    assert grid.splitlines()[-1] == "verdict at 3: expected on=block / off=allow, observed on=allow / off=allow"


def test_a_line_flags_one_leg_only(tmp_path: Path):
    with pytest.raises(ValueError, match="more than one leg is flagged"):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block, 'off': allow}\n  edges: []\n",
                        line="U>A[s1 -], A>T1[s1 -]⚑, T1>V1[s1 -]⚑"))


def test_flagged_line_needs_a_verdict(tmp_path: Path):
    with pytest.raises(ValueError, match="needs both"):
        load_case(_yaml(tmp_path, "expect:\n  edges: []\n"))
    with pytest.raises(ValueError, match="needs both"):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block}\n  edges: []\n"))
    with pytest.raises(ValueError, match="no leg is flagged"):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': allow, 'off': allow}\n  edges: []\n", line="U>A[s1 -]"))


def test_verdict_keys_must_be_quoted(tmp_path: Path):
    with pytest.raises(ValueError, match="quoted"):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {on: block, off: allow}\n  edges: []\n"))
