"""The judge without containers: the branches no shipped case exercises."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from . import caseline as cl
from .cases import CASES_DIR, Case, load_case
from .engine import LegRows, Observation
from .grid import render_grid
from .report import judge

LINE = "U>A[s1 -], A>T1[s1 -], T1>V1[s1 -]⚑"


def _case(**over) -> Case:
    base = dict(id="J", title="", cell="", line_text=LINE, line=cl.parse(LINE), units={"s1": "MRN"},
                verdict={"on": "block", "off": "allow"}, edges={("s1", 1, 2), ("s1", 2, 3)}, also=set(), cannot=None,
                pending=None, must_see={}, must_miss={}, authored=False, notes="", dir=Path("."))
    base.update(over)
    base.setdefault("accepts", {u: frozenset({t}) for u, t in base["units"].items()})
    return Case(**base)


def _obs(mode: str, edges: set, verdict: str, legs: dict | None = None) -> Observation:
    return Observation(mode=mode, legs=legs or {}, edges=edges, raw_edges=[], verdict=verdict, timings={},
                       neighbourhood={}, opa_calls=0)


def _found(index: int, *tags: str) -> LegRows:
    return LegRows(leg_index=index, interaction_id=None, leg_type="request", payload_hash=None,
                   classification={"sensitivity": "RESTRICTED", "findings": [(t, "v") for t in tags]},
                   inherited=[], risk=None)


def test_green_when_edges_and_verdicts_match():
    out = judge(_case(), _obs("on", {("s1", 1, 2), ("s1", 2, 3)}, "block"), _obs("off", set(), "allow"))
    assert out.status == "GREEN" and not out.reasons


def test_pending_case_that_passes_is_red():
    out = judge(_case(pending="waits"), _obs("on", {("s1", 1, 2), ("s1", 2, 3)}, "block"), _obs("off", set(), "allow"))
    assert out.status == "RED" and out.reasons[0].startswith("passes while marked pending")


def test_pending_case_that_fails_is_pending():
    out = judge(_case(pending="waits"), _obs("on", {("s1", 1, 2)}, "allow"), _obs("off", set(), "allow"))
    assert out.status == "PENDING" and out.reasons[0] == "pending: waits"


def test_extra_edge_is_red_unless_the_case_declares_it():
    edges = {("s1", 1, 2), ("s1", 2, 3), ("s1", 1, 3)}
    declared = judge(_case(also={("s1", 1, 3)}), _obs("on", edges, "block"), _obs("off", set(), "allow"))
    assert declared.status == "GREEN" and declared.origin_edges == {("s1", 1, 3)}   # 1>3 beside the expected 2>3
    undeclared = judge(_case(), _obs("on", edges, "block"), _obs("off", set(), "allow"))
    assert undeclared.status == "RED" and any(r.startswith("edges extra") for r in undeclared.reasons)


@pytest.mark.parametrize("extra", [("s1", 3, 7), ("s1", 3, 10), ("s1", 1, 4)])
def test_a_cross_call_edge_is_red_even_when_an_expected_edge_reaches_the_same_leg(extra):
    # P3: leg 3 is A1's call on T1, leg 7 T1's request inside A2's call; nothing crosses calls (#348).
    p3 = load_case(CASES_DIR / "P3")
    out = judge(p3, _obs("on", p3.expected_edges | {extra}, "block"), _obs("off", set(), "allow"))
    assert out.status == "RED" and out.edge_diff[1] == {extra}


def test_derived_edges_are_compared_too():
    derived = _case(edges=None)
    assert derived.expected_edges   # the line's naive chain
    out = judge(derived, _obs("on", set(), "block"), _obs("off", set(), "allow"))
    assert out.status == "RED" and any(r.startswith("edges missing") for r in out.reasons)


def test_a_unit_counts_as_labelled_under_any_of_its_types_everywhere():
    case = _case(units={"s1": "NAME"}, accepts={"s1": frozenset({"NAME", "PN"})},
                 must_see={1: ["s1"], 2: ["s1"]}, must_miss={3: ["s1"]})
    legs = {1: _found(1, "PN"), 2: _found(2, "NAME"), 3: _found(3, "PN")}
    out = judge(case, _obs("on", case.expected_edges, "block", legs), _obs("off", set(), "allow"))
    assert out.classifier_notes == [
        "leg 3: the model DID label s1 (NAME|PN); the case assumed it would not"]   # must_see at 1 and 2 met
    grid = render_grid(case, _obs("on", case.expected_edges, "block", legs), None)
    observed = grid.split("observed\n", 1)[1]
    assert observed.splitlines()[2].rstrip().endswith("●")   # the grid labels leg 3 by the same rule


def test_a_type_the_unit_does_not_list_does_not_count():
    case = _case(units={"s1": "NAME"}, must_see={1: ["s1"]}, must_miss={3: ["s1"]})
    legs = {1: _found(1, "PN"), 3: _found(3, "PN")}
    out = judge(case, _obs("on", case.expected_edges, "block", legs), _obs("off", set(), "allow"))
    assert out.classifier_notes == ["leg 1: the model did NOT label s1 (NAME); found ['PN']"]


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
    (tmp_path / "case.yaml").write_text(
        f"id: Y\nline: \"{LINE}\"\nunits: {{s1: MRN}}\nexpect:\n  verdict: {{'on': block, 'off': allow}}\n"
        f"classifier:\n  must_see: {see}\n  must_miss: {miss}\n")
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(tmp_path)


def test_a_unit_may_list_several_types(tmp_path: Path):
    (tmp_path / "case.yaml").write_text(
        f"id: Y\nline: \"{LINE}\"\nunits: {{s1: [NAME, PN]}}\nexpect:\n  verdict: {{'on': block, 'off': allow}}\n"
        f"classifier:\n  must_see: {{1: [s1], 2: [s1], 3: [s1]}}\n")
    case = load_case(tmp_path)
    assert case.units == {"s1": "NAME"} and case.accepts == {"s1": frozenset({"NAME", "PN"})}


def test_the_report_draws_the_expected_grid_above_the_observed():
    grid = render_grid(_case(), _obs("on", set(), "allow"), _obs("off", set(), "allow"))
    expected, observed = grid.split("\n\nobserved\n")
    assert expected.startswith("expected\n")
    assert "━" in expected and "━" not in observed.split("verdict")[0]
    assert grid.splitlines()[-1] == "verdict at 3: expected on=block / off=allow, observed on=allow / off=allow"


@pytest.mark.parametrize("on, off, reason", [
    ("allow", "allow", "verdict on: want block, got allow"),
    ("block", "block", "verdict off: want allow, got block"),
])
def test_a_wrong_verdict_is_red_in_either_mode(on, off, reason):
    out = judge(_case(), _obs("on", {("s1", 1, 2), ("s1", 2, 3)}, on), _obs("off", set(), off))
    assert out.status == "RED" and reason in out.reasons


def test_a_failure_the_case_says_no_observer_can_avoid_is_red_by_design():
    out = judge(_case(cannot="the value leaves through a hop no sidecar sees"),
                _obs("on", set(), "allow"), _obs("off", set(), "allow"))
    assert out.status == "RED-BY-DESIGN" and out.reasons[0].startswith("cannot: ")


def test_a_must_see_unit_the_model_missed_is_noted():
    case = _case(must_see={2: ["s1"]})
    out = judge(case, _obs("on", case.expected_edges, "block", {2: _found(2, "PHONE")}), _obs("off", set(), "allow"))
    assert out.classifier_notes == ["leg 2: the model did NOT label s1 (MRN); found ['PHONE']"]


def test_the_derived_rule_draws_no_edge_across_an_invisible_hop():
    line = cl.parse("U>A[s1 -], A>T1[s1 -], ..V1.., T1>A[s1 -]")
    assert cl.naive_expected_edges(line) == {("s1", 1, 2)}


def test_a_line_with_no_flagged_leg_is_judged_on_its_edges_alone():
    line = "U>A[s1 -], A>T1[s1 -]"
    case = _case(line_text=line, line=cl.parse(line), verdict={}, edges={("s1", 1, 2)})
    assert judge(case, _obs("on", {("s1", 1, 2)}, None), _obs("off", set(), None)).status == "GREEN"
    assert judge(case, _obs("on", set(), None), _obs("off", set(), None)).status == "RED"


def test_a_line_flags_one_leg_only(tmp_path: Path):
    two = "U>A[s1 -], A>T1[s1 -]⚑, T1>V1[s1 -]⚑"
    (tmp_path / "case.yaml").write_text(
        f"id: Y\nline: \"{two}\"\nunits: {{s1: MRN}}\nexpect:\n  verdict: {{'on': block, 'off': allow}}\n")
    with pytest.raises(ValueError, match="more than one leg is flagged"):
        load_case(tmp_path)


def test_flagged_line_needs_a_verdict(tmp_path: Path):
    (tmp_path / "case.yaml").write_text(f"id: Y\nline: \"{LINE}\"\nunits: {{s1: MRN}}\nexpect:\n  edges: derived\n")
    with pytest.raises(ValueError, match="needs both"):
        load_case(tmp_path)
    (tmp_path / "case.yaml").write_text(f"id: Y\nline: \"{LINE}\"\nunits: {{s1: MRN}}\nexpect:\n  verdict: {{'on': block}}\n")
    with pytest.raises(ValueError, match="needs both"):
        load_case(tmp_path)
    (tmp_path / "case.yaml").write_text("id: Y\nline: \"U>A[s1 -]\"\nunits: {s1: MRN}\nexpect:\n  verdict: {'on': allow, 'off': allow}\n")
    with pytest.raises(ValueError, match="no leg is flagged"):
        load_case(tmp_path)


def test_verdict_keys_must_be_quoted(tmp_path: Path):
    (tmp_path / "case.yaml").write_text(
        f"id: Y\nline: \"{LINE}\"\nunits: {{s1: MRN}}\nexpect:\n  verdict: {{on: block, off: allow}}\n  edges: derived\n")
    with pytest.raises(ValueError, match="quoted"):
        load_case(tmp_path)
