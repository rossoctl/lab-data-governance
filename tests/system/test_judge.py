"""The judge, the loader and the renderer without containers: every rule that refuses to guess, and the branches
no shipped case exercises."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from . import caseline as cl
from .cases import CASES_DIR, Case, Pending, load_case
from .classify import check_authored
from .engine import LegRows, Observation, flagged_response_problems, resolve_edges, verdict_at
from .grid import render_grid
from .render import render
from .report import judge

LINE = "U>A[s1 -], A>T1[s1 -], T1>V1[s1 -]⚑, V1>T1[-], T1>A[-], A>U[-]"
EDGES = {("s1", 1, 2), ("s1", 2, 3)}
MRN = "4471820"   # s1's bytes as an MRN


def _case(**over) -> Case:
    base = dict(id="J", title="", cell="", line_text=LINE, line=cl.parse(LINE), units={"s1": "MRN"},
                verdict={"on": "block", "off": "allow"}, edges=set(EDGES), pending=None,
                must_see={}, must_miss={}, authored=False, notes="", dir=Path("."))
    base.update(over)
    base.setdefault("accepts", {u: frozenset({t}) for u, t in base["units"].items()})
    return Case(**base)


def _obs(mode: str, edges: set, verdict: str | None, legs: dict | None = None, problems=()) -> Observation:
    return Observation(mode=mode, legs=legs or {}, edges=edges, raw_edges=[], verdict=verdict, timings={},
                       observer_calls=0, problems=list(problems))


def _at(index: int, *findings: tuple[str, str]) -> LegRows:
    return LegRows(leg_index=index, interaction_id=None, leg_type="request", payload_hash=None,
                   classification={"sensitivity": "RESTRICTED", "findings": list(findings)}, inherited=[], risk=None)


def _judge(case: Case, on_edges=EDGES, on="block", off="allow", legs=None, problems=(), off_edges=None):
    return judge(case, _obs("on", set(on_edges), on, legs, problems),
                 _obs("off", set(on_edges if off_edges is None else off_edges), off, legs))


def _yaml(tmp_path: Path, body: str, line: str = LINE, units: str = "{s1: MRN}") -> Path:
    d = tmp_path / "Y"
    d.mkdir(exist_ok=True)
    (d / "case.yaml").write_text(f"id: Y\nline: \"{line}\"\nunits: {units}\n" + body)
    return d


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


def test_a_problem_reading_the_rows_is_red():
    out = _judge(_case(), problems=["edge s1 1>None (MRN) has an end on no line leg"])
    assert out.status == "RED" and out.reasons == ["on: edge s1 1>None (MRN) has an end on no line leg"]


# --- a pending case is excused for exactly what it states -----------------------------------------------------

WAITS = Pending("waits", missing=frozenset({("s1", 2, 3)}), verdict=frozenset({"on"}))


def test_a_pending_case_that_fails_exactly_as_stated_is_pending():
    out = _judge(_case(pending=WAITS), on_edges={("s1", 1, 2)}, on="allow")
    assert out.status == "PENDING" and out.reasons[0] == "pending: waits"


def test_a_pending_case_that_fails_otherwise_is_red():
    out = _judge(_case(pending=WAITS), on_edges=set(), on="allow")   # 1>2 is missing too, which pending does not state
    assert out.status == "RED" and out.reasons[0].startswith("differs from what pending states")


def test_a_pending_case_that_passes_is_red():
    out = _judge(_case(pending=WAITS))
    assert out.status == "RED" and out.reasons[0].startswith("passes while marked pending")


def test_a_problem_reading_the_rows_is_red_even_for_a_pending_case():
    out = _judge(_case(pending=WAITS), on_edges={("s1", 1, 2)}, on="allow",
                 problems=["edge s1 1>None (MRN) has an end on no line leg"])
    assert out.status == "RED" and out.reasons[0] == "on: edge s1 1>None (MRN) has an end on no line leg"


@pytest.mark.parametrize("pending, error", [
    ("waits", "states reason and exactly what it gets wrong"),
    ("{reason: a, verdict: [both]}", "verdict lists modes 'on'/'off'"),
    ("{reason: a, missing: [s1 2>3]}", "pending missing edges that expect.edges does not list"),
    ("{reason: a, extra: [s1 1>2]}", "pending extra edges that expect.edges lists"),
])
def test_pending_must_state_exactly_what_it_excuses(tmp_path: Path, pending, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block, 'off': allow}\n  edges: [s1 1>2]\n"
                                  f"  pending: {pending}\nclassifier:\n  must_see: {{1: [s1], 2: [s1], 3: [s1]}}\n"))


# --- the classifier premise -------------------------------------------------------------------------------------

def test_a_unit_counts_as_labelled_under_any_of_its_types_everywhere():
    case = _case(units={"s1": "NAME"}, accepts={"s1": frozenset({"NAME", "PN"})},
                 must_see={1: ["s1"], 2: ["s1"]}, must_miss={3: ["s1"]})
    name = case.values["s1"]
    legs = {1: _at(1, ("PN", name)), 2: _at(2, ("NAME", name)), 3: _at(3, ("PN", name))}
    out = _judge(case, legs=legs)
    assert out.status == "RED" and out.classifier_notes == [
        f"{mode}: leg 3: the model DID label s1 (NAME|PN); the case assumed it would not"
        for mode in ("on", "off")]   # must_see at 1 and 2 met
    observed = render_grid(case, _obs("on", EDGES, "block", legs), None).split("observed\n", 1)[1]
    assert [ch for ch in observed.splitlines()[2] if ch in "●○"] == ["●", "●", "●"]   # leg 3 by the same rule


def test_a_type_the_unit_does_not_list_does_not_count():
    case = _case(units={"s1": "NAME"}, must_see={1: ["s1"]})
    out = _judge(case, legs={1: _at(1, ("PN", case.values["s1"]))})
    assert out.status == "RED" and out.classifier_notes == [
        f"{mode}: leg 1: the model did NOT label s1 (NAME); found [('PN', 'Rosa Alvarez')]" for mode in ("on", "off")]


def test_a_finding_on_other_bytes_does_not_label_the_unit():
    case = _case(must_see={1: ["s1"]}, must_miss={2: ["s1"]})
    legs = {1: _at(1, ("MRN", "9999999")), 2: _at(2, ("MRN", "9999999"))}   # right type, another value
    out = _judge(case, legs=legs)
    assert out.classifier_notes == [f"{mode}: leg 1: the model did NOT label s1 (MRN); found [('MRN', '9999999')]"
                                    for mode in ("on", "off")]


def test_a_stated_leg_with_no_classification_row_is_red():
    out = _judge(_case(must_miss={2: ["s1"]}), legs={})
    assert out.status == "RED" and out.classifier_notes == [
        f"{mode}: leg 2: no classification row; the case states s1 there" for mode in ("on", "off")]


def test_the_off_run_is_held_to_the_premise_too():
    case = _case(must_miss={2: ["s1"]})
    out = judge(case, _obs("on", EDGES, "block", {2: _at(2)}), _obs("off", EDGES, "allow", {}))
    assert out.status == "RED" and out.classifier_notes == ["off: leg 2: no classification row; the case states s1 there"]


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
    case = load_case(_yaml(tmp_path, "expect:\n  verdict: {'on': block, 'off': allow}\n  edges: []\n"
                                     "classifier:\n  must_see: {1: [s1], 2: [s1], 3: [s1]}\n", units="{s1: [NAME, PN]}"))
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
    resolved, _, _, problems = resolve_edges(case, [("i1", "request", "i9", "response", "MRN", MRN, MRN, "whole")],
                                             leg_of_key)
    assert resolved == set() and problems == ["edge s1 1>None (MRN) has an end on no line leg"]


def test_an_edge_carrying_another_type_is_a_problem():
    case = _case()
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    resolved, _, _, problems = resolve_edges(case, [("i1", "request", "i2", "request", "SSN", MRN, MRN, "whole")],
                                             leg_of_key)
    assert resolved == {("s1", 1, 2)} and problems == ["edge s1 1>2 carries SSN, not one of ['MRN']"]


@pytest.mark.parametrize("matched, kind", [("4471", "whole"), (MRN, "fragment")])
def test_an_edge_whose_evidence_is_not_the_whole_value_is_a_problem(matched, kind):
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    _, _, _, problems = resolve_edges(_case(), [("i1", "request", "i2", "request", "MRN", MRN, matched, kind)],
                                      leg_of_key)
    assert problems == [f"edge s1 1>2 matched {matched!r} as {kind!r}, not the whole value"]


def test_an_edge_on_bytes_of_no_unit_is_an_extra():
    case = _case()
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    resolved, _, _, _ = resolve_edges(case, [("i1", "request", "i2", "request", "MRN", "123", "123", "whole")],
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
    ("U>A[s1@id s2@id -]", "two values under the key 'id'"),
    ("U>A[s1@note -]", "key 'note' is reserved for filler"),
])
def test_a_payload_never_loses_a_value(line, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        render("Y", {"s1": "MRN", "s2": "SSN"}, cl.parse(line))


@pytest.mark.parametrize("line, error", [
    ("U>A[-], T1>A[s1 -]", "T1>A answers no open request"),
    ("U>A1[-], U>A2[-], A1>T1[-], A2>T1[-], T1>V1[s1 -]", "T1 is serving 2 calls (A1, A2)"),
    ("U>A[-], A>T1[-], A>T1[-], T1>A[s1 -]", "T1>A could answer 2 open calls A>T1"),
])
def test_an_ambiguous_line_is_refused(line, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        render("Y", {"s1": "MRN"}, cl.parse(line))


def test_a_named_call_resolves_the_ambiguity():
    r = render("Y", {"s1": "MRN"}, cl.parse("U>A1[-], U>A2[-], A1>T1[-], A2>T1[-], T1(A1)>V1[s1 -], V1>T1[-], "
                                            "T1>A1[-], T1>A2[-], A1>U[-], A2>U[-]"))
    assert [x.leg.index for x in r.rendered] == list(range(1, 11))


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


def test_the_case_record_carries_no_timing():
    from .report import case_markdown
    obs = _obs("on", EDGES, "block")
    obs.timings = {"reset": 0.1, "total": 0.1}
    record = case_markdown(judge(_case(), obs, _obs("off", EDGES, "allow")))
    assert "0.1" not in record and "Risk observer calls: 0" in record


# --- every remaining refusal ----------------------------------------------------------------------------------

SEE_ALL = "classifier:\n  must_see: {1: [s1], 2: [s1], 3: [s1]}\n"
VERDICT = "  verdict: {'on': block, 'off': allow}\n"


@pytest.mark.parametrize("expect, error", [
    ("  edges: [s2 1>2]\n", "naming an undeclared unit or a leg the line lacks"),
    ("  edges: [s1 1>9]\n", "naming an undeclared unit or a leg the line lacks"),
])
def test_the_loader_refuses_an_inconsistent_expectation(tmp_path: Path, expect, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + expect + SEE_ALL))


def test_an_expected_edge_joins_legs_that_carry_its_unit(tmp_path: Path):
    line = "U>A[s1 -], A>T1[-], T1>V1[s1 -]⚑"
    with pytest.raises(ValueError, match=re.escape("legs that do not both carry the unit: [('s1', 1, 2)]")):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: [s1 1>2]\n"
                                  "classifier:\n  must_see: {1: [s1], 3: [s1]}\n", line=line))


@pytest.mark.parametrize("line, error", [
    ("U>A1[-], U>A2[-], A1>T1[-], T1(A3)>V1[s1 -]", "0 open calls from A3 on T1"),
    ("U>A[-], A>T1[-], T1(A)>A[s1 -]", "(caller) belongs on a request"),
    ("U>A[-], A>T1[-], V1>T1[s1 -]", "V1>T1 answers no open request"),
    ("U>A[-], A>U[-], A>T1[s1 -]", "A sends A>T1 while serving no call"),
    ("A>T1[-], T1>A[-]", "a conversation starts with the user"),
    ("U>A[-], A>T1[-]", "requests the conversation never answers: 1 U>A, 2 A>T1"),
    ("U>A[-], A>T1[-], T1>A[-]⚑, A>U[-]", "⚑ marks a request; T1>A is a response"),
    ("U>A[-], A>T1[-], T1>V1[-]⚑, V1>T1[s1 -], T1>A[-], A>U[-]", "the flagged exchange's response carries s1"),
    ("Q>A[-]", "unknown party 'Q'"),
    ("U>A[y1]", "bad element 'y1'"),
    ("U>A[-", "bad leg"),
    ("[-]", "leg needs parties"),
    ("U>A[s2 -], A>U[-]", "unit s2 is not declared in units"),
])
def test_the_renderer_refuses_a_line_it_would_have_to_guess(line, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        render("Y", {"s1": "MRN"}, cl.parse(line))


def test_a_leg_the_derivation_never_produced_is_a_problem():
    from .engine import map_legs
    r = render("Y", {"s1": "MRN"}, cl.parse(LINE))
    ix_of = {x.request_span_id: f"i{x.request_span_id}" for x in r.rendered}
    hash_of = {(ix_of[x.request_span_id], x.role): f"h{x.leg.index}" for x in r.rendered}
    payloads = set(hash_of.values())
    _, _, ok = map_legs(r.rendered, ix_of, hash_of, payloads)
    assert ok == []
    gone = next(x for x in r.rendered if x.leg.index == 2)
    for broken, what in (({**ix_of, gone.request_span_id: None}, "interaction"),):
        iid = ix_of[gone.request_span_id]
        assert map_legs(r.rendered, {k: v for k, v in broken.items() if v}, hash_of, payloads)[2] == [
            f"leg 2 (A>T1) was not derived: no {what}", f"leg 5 (T1>A) was not derived: no {what}",   # one exchange
            f"a derived request row of interaction {iid} sits on no line leg",
            f"a derived response row of interaction {iid} sits on no line leg"]
    row = (ix_of[gone.request_span_id], gone.role)
    assert map_legs(r.rendered, ix_of, {k: v for k, v in hash_of.items() if k != row}, payloads)[2] == [
        "leg 2 (A>T1) was not derived: no leg row"]
    assert map_legs(r.rendered, ix_of, hash_of, payloads - {"h2"})[2] == ["leg 2 (A>T1) was not derived: no payload"]


SHARED = "U>A[s1 s2], A>T1[s1 s2], T1>A[-], A>U[-]"   # legs 1 and 2 carry one payload text


@pytest.mark.parametrize("see, miss, error", [
    ({1: ["s1"], 2: ["s1"]}, {1: ["s2"], 2: ["s2"]}, None),            # s2 missed on both legs of the text
    ({1: ["s1", "s2"], 2: ["s1"]}, {2: ["s2"]}, "leg 1 must see s2"),   # leg 2's subtraction would take leg 1's s2
    ({1: ["s1"], 2: ["s1", "s2"]}, {1: ["s2"]}, "leg 2 must see s2"),
    ({1: ["s2"], 2: ["s2"]}, {1: ["s1"], 2: ["s1"]}, None),
])
def test_authored_subtraction_never_takes_a_value_a_must_see_leg_of_the_same_text_needs(see, miss, error):
    from .classify import authored_map
    case = _case(line_text=SHARED, line=cl.parse(SHARED), units={"s1": "MRN", "s2": "SSN"}, verdict={}, edges=set(),
                 must_see=see, must_miss=miss, authored=True)
    renderer = render(case.id, case.units, case.line)
    if error:
        with pytest.raises(ValueError, match=error):
            authored_map(case, renderer)
    else:
        (values,) = authored_map(case, renderer).values()
        assert values == {case.values[u] for u in miss[1]}


def test_authored_subtraction_takes_one_unit_and_leaves_the_others_of_its_payload():
    """One payload, s2 missed, s1 and s3 seen. Only s2's findings go; a finding overlapping
    s2's bytes without being them is not s2's and stays (it labels no unit either way)."""
    from .classify import authored_map, subtract
    line = "U>A[s1 s2 s3], A>U[-]"
    case = _case(line_text=line, line=cl.parse(line), units={"s1": "SSN", "s2": "MRN", "s3": "DOB"}, verdict={},
                 edges=set(), must_see={1: ["s1", "s3"]}, must_miss={1: ["s2"]}, authored=True)
    renderer = render(case.id, case.units, case.line)
    ((text, values),) = authored_map(case, renderer).items()
    assert values == {case.values["s2"]}
    at = {u: text.index(v) for u, v in case.values.items()}
    own = {u: [at[u], at[u] + len(v), case.units[u]] for u, v in case.values.items()}
    overlap = [at["s2"] - 2, at["s2"] + 3, "MRN"]
    by_text = {text: [own["s1"], own["s2"], own["s3"], overlap]}
    assert subtract(by_text, text, values) == 1
    assert by_text[text] == [own["s1"], own["s3"], overlap]
    findings = [(t, text[a:b]) for a, b, t in by_text[text]]
    assert [case.labelled(u, findings) for u in ("s1", "s2", "s3")] == [True, False, True]


def test_a_case_cannot_have_more_units_of_a_type_than_values():
    with pytest.raises(ValueError, match="more than 2 units of SSN"):
        render("Y", {"s1": "SSN", "s2": "SSN", "s3": "SSN"}, cl.parse(LINE))


def test_an_uncalibrated_payload_fails_loudly():
    from .classify import ReplayDetector, UncalibratedPayload
    with pytest.raises(UncalibratedPayload, match="not in findings.json"):
        ReplayDetector({"by_text": {}}).detect("a payload nobody calibrated")


@pytest.mark.parametrize("body, error", [
    ("notes: a\nnote: b\n", "unknown top-level keys ['note']"),
    ("classifier:\n  must_see: {1: [s1], 2: [s1], 3: [s1]}\n  authord: true\n", "unknown classifier.keys ['authord']"),
    ("notes: a\nnotes: b\n", "duplicated key(s) ['notes']"),
])
def test_the_loader_refuses_a_typo(tmp_path: Path, body, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n" + body))


def test_a_case_id_is_its_directory_name(tmp_path: Path):
    d = _yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n" + SEE_ALL)
    moved = d.rename(tmp_path / "Z")
    with pytest.raises(ValueError, match="id 'Y' is not the case directory's name"):
        load_case(moved)


def test_the_flagged_exchange_response_must_carry_no_finding():
    r = render("Y", {"s1": "MRN"}, cl.parse(LINE))
    clean = {4: _at(4)}
    assert flagged_response_problems(r.rendered, clean, 3) == []
    assert flagged_response_problems(r.rendered, {}, 3) == [
        "the flagged exchange's response (leg 4) has no classification row"]
    assert flagged_response_problems(r.rendered, {4: _at(4, ("DATE", "02:00"))}, 3) == [
        "the flagged exchange's response (leg 4) was labelled [('DATE', '02:00')]; the exchange's verdict would rest on it"]


@pytest.mark.parametrize("cursor, queued, problems", [
    (16, {}, []),
    (14, {}, ["incomplete run: leg_ready stopped at cursor 14 of 16"]),
    (16, {"leg_ready": 3}, ["incomplete run: leg_ready left 3 row(s) in risk_recompute_requests"]),
    (16, {"label_lineage": 1}, ["incomplete run: label_lineage left 1 row(s) in label_lineage_dirty_traces"]),
])
def test_a_drain_that_did_not_finish_is_a_problem(cursor, queued, problems):
    from .engine import QUEUES, STREAMS, unfinished
    heads = {s: 16 for s in STREAMS}
    assert unfinished({**heads, "leg_ready": cursor}, heads, {**{q: 0 for q in QUEUES}, **queued}) == problems
    assert _judge(_case(), problems=problems).status == ("RED" if problems else "GREEN")


def test_derived_rows_and_line_legs_map_one_to_one():
    from .engine import map_legs
    r = render("Y", {"s1": "MRN"}, cl.parse(LINE))
    ix_of = {x.request_span_id: f"i{x.request_span_id}" for x in r.rendered}
    hash_of = {(ix_of[x.request_span_id], x.role): f"h{x.leg.index}" for x in r.rendered}
    orphan = {**hash_of, ("i-elsewhere", "request"): "h99"}
    assert map_legs(r.rendered, ix_of, orphan, set(orphan.values()))[2] == [
        "a derived request row of interaction i-elsewhere sits on no line leg"]
    one = next(x for x in r.rendered if x.leg.index == 2)
    two = next(x for x in r.rendered if x.leg.index == 3)
    merged = {**ix_of, two.request_span_id: ix_of[one.request_span_id]}   # legs 3 and 6 fold into 2 and 5's rows
    assert "legs 2 and 3 land on one derived request row" in map_legs(r.rendered, merged, hash_of, set(hash_of.values()))[2]


@pytest.mark.parametrize("body, error", [
    ("expect:\n" + VERDICT + "  edges: [s1 1>2, s1 1>2]\n" + SEE_ALL, "expect.edges lists edges twice"),
    ("expect:\n" + VERDICT + "  edges: [s1 2>1]\n" + SEE_ALL, "edges that do not run forward"),
    ("expect:\n" + VERDICT + "  edges: []\nclassifier:\n  must_see: {1: [s1], '1': [s1], 2: [s1], 3: [s1]}\n",
     "classifier.must_see names leg 1 twice"),
    ("expect:\n" + VERDICT + "  edges: []\nclassifier:\n  must_see: {1: [s1, s1], 2: [s1], 3: [s1]}\n",
     "lists a unit twice at leg 1"),
    ("expect:\n" + VERDICT + "  edges: []\n" + SEE_ALL + "  authored: 'false'\n", "classifier.authored is true or false"),
])
def test_the_loader_refuses_what_it_would_have_to_merge(tmp_path: Path, body, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, body))


@pytest.mark.parametrize("line, error", [
    ("U>A[-], A>T1[-], ..Q.., T1>A[-], A>U[-]", "unknown party 'Q'"),
    ("U>A[-], A>T1[-], T1>V1[-], T1>A[-], V1>T1[-], A>U[-]", "T1 answers while its call 3 T1>V1 is still open"),
])
def test_the_line_grammar_refuses_what_cannot_happen(line, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        render("Y", {"s1": "MRN"}, cl.parse(line))


def test_a_tool_may_answer_one_caller_while_serving_another():
    line = ("U>A1[-], U>A2[-], A1>T1[-], A2>T1[-], T1(A2)>V2[-], T1>A1[s1 -], V2>T1[-], T1>A2[-], A1>U[-], A2>U[-]")
    assert len(render("Y", {"s1": "MRN"}, cl.parse(line)).rendered) == 10
