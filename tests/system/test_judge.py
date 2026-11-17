"""The judge, the loader and the renderer without containers: every rule that refuses to guess, and the branches
no shipped case exercises."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from . import caseline as cl
from .cases import CASES_DIR, MODES, Case, Pending, load_case
from .classify import check_authored
from .engine import LegRows, Observation, flagged_response_problems, resolve_edges, verdict_at
from .grid import render_grid
from .render import render
from .report import judge

LINE = "U>A[s1 -], A>T1[s1 -], T1>V1[s1 -]⚑, V1>T1[-], T1>A[-], A>U[-]"
EDGES = {("s1", 1, 2, None), ("s1", 2, 3, None)}
MRN = "4471820"   # s1's bytes as an MRN
VERDICT_OF = {"off": "allow", "conv": "block", "cross": "block"}


def _case(**over) -> Case:
    base = dict(id="J", title="", cell="", line_text=LINE, line=cl.parse(LINE), units={"s1": "MRN"},
                verdict=dict(VERDICT_OF), edges=set(EDGES), pending=None,
                must_see={}, must_miss={}, authored=False, notes="", dir=Path("."))
    base.update(over)
    base.setdefault("accepts", {u: frozenset({t}) for u, t in base["units"].items()})
    return Case(**base)


def _obs(mode: str, edges: set, verdict: str | None, legs: dict | None = None, problems=(), evidence=None,
         marks=None) -> Observation:
    return Observation(mode=mode, legs=legs or {}, edges=set(edges), raw_edges=[], verdict=verdict, timings={},
                       observer_calls=0, problems=list(problems), evidence=evidence or {}, marks=marks or {})


def _at(index: int, *findings: tuple[str, str]) -> LegRows:
    return LegRows(leg_index=index, interaction_id=None, leg_type="request", payload_hash=None,
                   classification={"sensitivity": "RESTRICTED", "findings": list(findings)}, inherited=[], risk=None)


def _judge(case: Case, cross_edges=None, verdicts=None, legs=None, problems=(), edges=None, evidence=None,
           marks=None):
    """The conv run draws *edges* (by default the case's own), the cross and off runs *cross_edges* (both with the
    cross switch on); *problems*, the evidence and the marks are the cross run's."""
    edges = case.edges if edges is None else edges
    cross_edges = edges | case.cross if cross_edges is None else cross_edges
    verdicts = {**VERDICT_OF, **(verdicts or {})}
    runs = {"off": _obs("off", cross_edges, verdicts["off"], legs), "conv": _obs("conv", edges, verdicts["conv"], legs)}
    runs["cross"] = _obs("cross", cross_edges, verdicts["cross"], legs, problems, evidence, marks)
    return judge(case, runs)


def _runs(edges, verdicts=VERDICT_OF, legs=None) -> dict[str, Observation]:
    return {m: _obs(m, edges, verdicts[m], legs) for m in MODES}


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
    out = _judge(_case(), cross_edges=EDGES | {("s1", 1, 3, None)})
    assert out.status == "RED" and "edges extra: s1 1>3" in out.reasons


@pytest.mark.parametrize("extra", [("s1", 3, 7, None), ("s1", 3, 10, None), ("s1", 1, 4, None)])
def test_a_cross_call_edge_is_red_even_when_an_expected_edge_reaches_the_same_leg(extra):
    # P3: leg 3 is A1's call on T1, leg 7 T1's request inside A2's call; nothing crosses calls (#348).
    p3 = load_case(CASES_DIR / "P3")
    out = _judge(p3, cross_edges=p3.edges | {extra}, legs=_p3_legs(p3))
    assert out.status == "RED" and out.edge_diff[1] == {extra}


def _p3_legs(case: Case) -> dict[int, LegRows]:
    value = case.values["s1"]
    legs = {i: _at(i, ("SSN", value)) for i in case.must_see}
    legs.update({i: _at(i) for i in case.must_miss})
    return legs


@pytest.mark.parametrize("verdicts, reason", [
    ({"conv": "allow"}, "verdict conv: want block, got allow"),
    ({"off": "block"}, "verdict off: want allow, got block"),
    ({"cross": "allow"}, "verdict cross: want block, got allow"),
    ({"conv": "warn"}, "verdict conv: want block, got warn"),   # compared as the record writes it
])
def test_a_wrong_verdict_is_red_in_any_mode(verdicts, reason):
    out = _judge(_case(), verdicts=verdicts)
    assert out.status == "RED" and reason in out.reasons


def test_a_line_with_no_flagged_leg_is_judged_on_its_edges_alone():
    line = "U>A[s1 -], A>T1[s1 -]"
    one = {("s1", 1, 2, None)}
    case = _case(line_text=line, line=cl.parse(line), verdict={}, edges=one)
    assert judge(case, _runs(one, dict.fromkeys(MODES))).status == "GREEN"
    assert judge(case, _runs(set(), dict.fromkeys(MODES))).status == "RED"


@pytest.mark.parametrize("mode", ["off", "conv"])
def test_a_run_with_the_cross_switch_off_draws_exactly_the_conversations_own_edges(mode):
    runs = _runs(EDGES)
    runs[mode] = _obs(mode, {("s1", 1, 2, None)}, VERDICT_OF[mode])
    out = judge(_case(), runs)
    assert out.status == "RED" and out.reasons[0].startswith(f"the {mode} run drew other edges than "
                                                             + ("expect.edges" if mode == "conv" else "the cross run"))


def test_the_off_run_draws_the_crossings_too_and_only_its_verdict_differs():
    """Inherited labels off, the cross switch on: label lineage draws the crossing, and no verdict may rest on it."""
    crossing = ("s1", 1, 3, "vo")
    case = _case(cross=frozenset({crossing}))
    assert _judge(case).status == "GREEN"
    runs = _runs(EDGES)
    runs["cross"] = _obs("cross", EDGES | {crossing}, "block")
    out = judge(case, runs)                                  # the off run without the crossing the cross run drew
    assert out.status == "RED" and out.reasons[0] == ("the off run drew other edges than the cross run (inherited "
                                                      "labels off changes the verdict only): missing s1 1>3 vo, "
                                                      "extra -")
    assert _judge(case, verdicts={"off": "block"}).reasons == ["verdict off: want allow, got block"]


def test_a_crossing_drawn_with_the_cross_switch_off_is_red_even_for_a_pending_case():
    crossing = ("s1", 1, 3, "vo")
    case = _case(cross=frozenset({crossing}), pending=Pending("waits", missing=frozenset({crossing})))
    runs = _runs(EDGES)
    runs["conv"] = _obs("conv", EDGES | {crossing}, "block")
    out = judge(case, runs)
    assert out.status == "RED" and out.reasons[0] == ("the conv run drew other edges than expect.edges (with the "
                                                      "cross switch off only the conversation's own): missing -, "
                                                      "extra s1 1>3 vo")


def test_only_the_cross_run_draws_the_crossings_and_what_rests_on_them():
    cross = frozenset({("s1", 1, 3, "co"), ("s1", 3, 4, None)})
    line = "U>A[s1 -], A>T1[s1 -], T1>V1[s1 -]⚑, V1>T1[s1 -], T1>A[-], A>U[-]"
    case = _case(line=cl.parse(line), line_text=line, cross=cross)
    assert _judge(case).status == "GREEN"
    assert _judge(case, cross_edges=EDGES).status == "RED"


def test_a_wrong_level_on_an_edge_is_red_and_named():
    case = _case(cross=frozenset({("s1", 1, 3, "co")}))
    out = _judge(case, cross_edges=EDGES | {("s1", 1, 3, "vo")})
    assert out.status == "RED"
    assert out.reasons[:3] == ["level of edge s1 1>3: want co, drawn vo", "edges missing: s1 1>3 co",
                               "edges extra: s1 1>3 vo"]
    plain = _judge(case, cross_edges=EDGES | {("s1", 1, 3, None)})    # the edge drawn inside the conversation
    assert plain.status == "RED" and plain.reasons[0] == "level of edge s1 1>3: want co, drawn none"


def test_a_problem_reading_the_rows_is_red():
    out = _judge(_case(), problems=["edge s1 1>None (MRN) has an end on no line leg"])
    assert out.status == "RED" and out.reasons == ["cross: edge s1 1>None (MRN) has an end on no line leg"]


@pytest.mark.parametrize("mode", MODES)
def test_a_problem_reading_the_rows_of_any_one_run_is_red(mode):
    runs = _runs(EDGES)
    runs[mode].problems.append("the flagged leg's record is missing")
    out = judge(_case(), runs)
    assert out.status == "RED" and out.reasons == [f"{mode}: the flagged leg's record is missing"]


def test_a_declared_key_no_derived_entity_has_is_a_problem():
    from .engine import undeclared
    declared = {"tool:lab/t2": "conversation", "T2": "conversation"}   # the second names a party, not an entity
    problems = undeclared(declared, {"tool:lab/t2", "agent:lab/a"})
    assert problems == ["scopes declares T2 as conversation, which is no derived entity"]
    assert undeclared(declared, {"tool:lab/t2", "T2"}) == [] and undeclared({}, set()) == []
    assert _judge(_case(), problems=problems).status == "RED"


# --- the evidence and the marks of the cross run -----------------------------------------------------------------

def test_the_stated_evidence_at_a_leg_is_compared_exactly():
    case = _case(evidence={3: {"s1": frozenset({"crossed vo"})}})
    assert _judge(case, evidence={3: {"s1": frozenset({"crossed vo"})}}).status == "GREEN"
    for got, shown in (({"inherited"}, "[inherited]"), ({"crossed co"}, "[crossed co]"),
                       ({"crossed vo", "inherited"}, "[crossed vo, inherited]"), (set(), "[]")):
        out = _judge(case, evidence={3: {"s1": frozenset(got)}})
        assert out.status == "RED" and out.reasons == [f"evidence at 3 s1: want [crossed vo], got {shown}"]


def test_a_crossed_label_at_a_cell_the_case_does_not_state_is_red_and_named():
    case = _case(evidence={3: {"s1": frozenset({"crossed vo"})}})
    spurious = {3: {"s1": frozenset({"crossed vo"})}, 2: {"s1": frozenset({"crossed co", "inherited"})}}
    out = _judge(case, evidence=spurious)
    assert out.status == "RED" and out.reasons == ["evidence at 2 s1: not stated, got [crossed co, inherited]"]
    assert _judge(case, evidence={**spurious, 2: {"s1": frozenset({"inherited", "classified MRN"})}}).status == "GREEN"
    waits = _case(evidence=case.evidence, pending=Pending("waits", evidence=frozenset({2})))
    assert _judge(waits, evidence=spurious).status == "PENDING"     # counts as wrong evidence at that leg


def test_a_stated_mark_and_an_unstated_one_are_both_compared():
    case = _case(marks={3: frozenset({"capped 4"})})
    assert _judge(case, marks={3: frozenset({"capped 4"})}).status == "GREEN"
    assert _judge(case, marks={3: frozenset({"capped 6"})}).reasons == ["marks at 3: want [capped 4], got [capped 6]"]
    out = _judge(_case(), marks={2: frozenset({"incomplete payload_bytes"})})
    assert out.status == "RED" and out.reasons == ["marks at 2: want [], got [incomplete payload_bytes]"]


# --- a pending case is excused for exactly what it states -----------------------------------------------------

WAITS = Pending("waits", missing=frozenset({("s1", 2, 3, None)}), verdict=frozenset({"cross"}))


def test_a_pending_case_that_fails_exactly_as_stated_is_pending():
    out = _judge(_case(pending=WAITS), cross_edges={("s1", 1, 2, None)}, verdicts={"cross": "allow"})
    assert out.status == "PENDING" and out.reasons[0] == "pending: waits"


def test_a_pending_case_that_fails_otherwise_is_red():
    out = _judge(_case(pending=WAITS), cross_edges=set(), verdicts={"cross": "allow"})   # 1>2 missing too: unstated
    assert out.status == "RED" and out.reasons[0].startswith("differs from what pending states")


def test_a_pending_case_that_passes_is_red():
    out = _judge(_case(pending=WAITS))
    assert out.status == "RED" and out.reasons[0].startswith("passes while marked pending")


def test_pending_evidence_and_marks_are_exact_too():
    case = _case(evidence={3: {"s1": frozenset({"crossed vo"})}}, marks={3: frozenset({"capped 4"})},
                 pending=Pending("waits", evidence=frozenset({3}), marks=frozenset({3})))
    assert _judge(case).status == "PENDING"
    assert _judge(case, marks={3: frozenset({"capped 4"})}).status == "RED"    # the marks are right after all


def test_a_problem_reading_the_rows_is_red_even_for_a_pending_case():
    out = _judge(_case(pending=WAITS), cross_edges={("s1", 1, 2, None)}, verdicts={"cross": "allow"},
                 problems=["edge s1 1>None (MRN) has an end on no line leg"])
    assert out.status == "RED" and out.reasons[0] == "cross: edge s1 1>None (MRN) has an end on no line leg"


TIERED = {"off": ("allow tier 1",), "conv": ("allow tier 1",), "cross": ("allow tier 1", "block tier 2")}


def test_a_held_case_is_pending_only_for_exactly_what_this_branch_lacks(monkeypatch):
    from . import engine
    from .report import judge_held
    # every path is on the branch now: the machinery is shown on the last one built, as if it were not
    monkeypatch.setitem(engine.NOT_ON_BRANCH, "tiers", "the verdict tiers: read by verdict_at")
    held = _case(verdict=TIERED, pending=Pending("waits", held=frozenset({"tiers"})))
    out = judge_held(held)
    assert out.status == "PENDING" and out.runs == {} and out.reasons[1].startswith("held, not run: needs the verdict")
    assert judge_held(_case(scopes={"V1": "call"}, verdict=TIERED,
                            pending=Pending("waits", held=frozenset({"scopes", "tiers"})))).status == "RED"
    assert judge_held(_case(verdict=TIERED)).status == "RED"      # no pending block at all
    ran = _judge(_case(scopes={"V1": "call"}, pending=Pending("waits", held=frozenset({"scopes"}))))   # the branch has it
    assert ran.status == "RED" and ran.reasons[0].startswith("held for ['scopes'], which this branch has")
    unused = _judge(_case(pending=Pending("waits", held=frozenset({"switch"}))))   # lacked or not, the case uses none
    assert unused.status == "RED" and unused.reasons[0].startswith(
        "held for ['switch'], but the case does not use ['switch']")


@pytest.mark.parametrize("case_id, want", [
    ("K12", {"service:api.v2.partner.example:8000": "call"}),
    ("K13", {"agent:lab/a": "persistent"}),
    ("K22a", {"tool:lab/t2": "persistent"}),
    ("K1", {}),
])
def test_a_case_declares_its_scopes_through_the_production_parser(case_id, want):
    from data_governance.processors.data_lineage import memory
    from .engine import declaration
    case = load_case(CASES_DIR / case_id)
    assert dict(memory.parse_scopes(declaration(case, render(case.id, case.units, case.line)))) == want


def test_every_party_kind_has_a_natural_key_the_parser_takes():
    from data_governance.processors.data_lineage import memory
    from .engine import declaration
    line = "U>A[-], A>L[-], L>A[-], A>T1[-], T1>V1[-], V1>T1[-], T1>A[-], A>U[-]"
    case = _case(line_text=line, line=cl.parse(line), edges=set(),
                 scopes={"U": "call", "A": "call", "L": "conversation", "T1": "persistent", "V1": "call"})
    assert dict(memory.parse_scopes(declaration(case, render(case.id, case.units, case.line)))) == {
        "client:(unknown)": "call", "agent:lab/a": "call",
        "llm:host.containers.internal:11434/qwen2.5:7b": "conversation", "tool:lab/t1": "persistent",
        "service:api.v1.partner.example:8000": "call"}


def test_a_case_uses_what_its_keys_and_steps_name():
    assert _case().uses == frozenset()
    assert _case(scopes={"V1": "call"}, config={"payload_bytes": 1}, steps=(("cross", "on"), ("reclassify", ())),
                 verdict={**VERDICT_OF, "cross": ("allow tier 1",)}).uses == {"scopes", "config", "switch",
                                                                              "reclassify", "tiers"}


@pytest.mark.parametrize("pending, error", [
    ("waits", "states reason and exactly what it gets wrong"),
    ("{reason: a, verdict: [both]}", "expect.pending.verdict names the modes"),
    ("{reason: a, missing: [s1 2>3]}", "pending missing edges that expect does not list"),
    ("{reason: a, extra: [s1 1>2]}", "pending extra edges that expect lists"),
    ("{reason: a, held: [scopes], verdict: [cross]}", "a held case is not run"),
    ("{reason: a, evidence: [9]}", "expect.pending.evidence names legs the line lacks: [9]"),
    ("{reason: '', verdict: [cross]}", "states reason and exactly what it gets wrong"),
    ("{reason: a, held: [crossing]}", "expect.pending.held lists production paths, each one of"),   # no path at all
    ("{reason: a, verdict: cross}", "Y: expect.pending.verdict names the modes ('off', 'conv', 'cross') in a list"),
    ("{reason: a, evidence: [x]}", "Y: expect.pending.evidence lists leg indices, got ['x']"),
])
def test_pending_must_state_exactly_what_it_excuses(tmp_path: Path, pending, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: [s1 1>2]\n"
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
        for mode in MODES]   # must_see at 1 and 2 met
    observed = render_grid(case, {"cross": _obs("cross", EDGES, "block", legs)}).split("observed\n", 1)[1]
    assert [ch for ch in observed.splitlines()[2] if ch in "●○"] == ["●", "●", "●"]   # leg 3 by the same rule


def test_a_type_the_unit_does_not_list_does_not_count():
    case = _case(units={"s1": "NAME"}, must_see={1: ["s1"]})
    out = _judge(case, legs={1: _at(1, ("PN", case.values["s1"]))})
    assert out.status == "RED" and out.classifier_notes == [
        f"{mode}: leg 1: the model did NOT label s1 (NAME); found [('PN', 'Rosa Alvarez')]" for mode in MODES]


def test_a_finding_on_other_bytes_does_not_label_the_unit():
    case = _case(must_see={1: ["s1"]}, must_miss={2: ["s1"]})
    legs = {1: _at(1, ("MRN", "9999999")), 2: _at(2, ("MRN", "9999999"))}   # right type, another value
    out = _judge(case, legs=legs)
    assert out.classifier_notes == [f"{mode}: leg 1: the model did NOT label s1 (MRN); found [('MRN', '9999999')]"
                                    for mode in MODES]


def test_a_stated_leg_with_no_classification_row_is_red():
    out = _judge(_case(must_miss={2: ["s1"]}), legs={})
    assert out.status == "RED" and out.classifier_notes == [
        f"{mode}: leg 2: no classification row; the case states s1 there" for mode in MODES]


def test_the_off_run_is_held_to_the_premise_too():
    case = _case(must_miss={2: ["s1"]})
    runs = _runs(EDGES, legs={2: _at(2)})
    runs["off"] = _obs("off", EDGES, "allow", {})
    out = judge(case, runs)
    assert out.status == "RED" and out.classifier_notes == ["off: leg 2: no classification row; the case states s1 there"]


def test_the_expected_grid_draws_must_see_full_and_must_miss_hollow():
    case = _case(must_see={1: ["s1"], 2: ["s1"]}, must_miss={3: ["s1"]})
    expected = render_grid(case, {}).split("\n\nobserved")[0]
    assert [ch for ch in expected.splitlines()[3] if ch in "●○"] == ["●", "●", "○"]


@pytest.mark.parametrize("see, miss, error", [
    ("{1: [s1], 2: [s1], 3: [s1]}", "{3: [s1]}", "in both must_see and must_miss: s1@3"),
    ("{1: [s1], 2: [s1]}", "{}", "in neither must_see nor must_miss: s1@3"),
    ("{1: [s1], 2: [s1], 3: [s1]}", "{4: [s1]}", "a unit the leg does not carry: s1@4"),
])
def test_every_present_unit_is_stated_exactly_once(tmp_path: Path, see, miss, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n"
                                  f"classifier:\n  must_see: {see}\n  must_miss: {miss}\n"))


def test_a_unit_may_list_several_types(tmp_path: Path):
    case = load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n"
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
    resolved, _, _, problems = resolve_edges(case, [("i1", "request", "i9", "response", "MRN", MRN, MRN, "whole", None, "o1", None)],
                                             leg_of_key)
    assert resolved == set() and problems == ["edge s1 1>None (MRN) has an end on no line leg"]


def test_an_edge_carrying_another_type_is_a_problem():
    case = _case()
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    resolved, _, _, problems = resolve_edges(case, [("i1", "request", "i2", "request", "SSN", MRN, MRN, "whole", None, "o1", None)],
                                             leg_of_key)
    assert resolved == {("s1", 1, 2, None)} and problems == ["edge s1 1>2 carries SSN, not one of ['MRN']"]


@pytest.mark.parametrize("matched, kind", [("4471", "whole"), (MRN, "fragment")])
def test_an_edge_whose_evidence_is_not_the_whole_value_is_a_problem(matched, kind):
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    _, _, _, problems = resolve_edges(_case(), [("i1", "request", "i2", "request", "MRN", MRN, matched, kind, None, "o1", None)],
                                      leg_of_key)
    assert problems == [f"edge s1 1>2 matched {matched!r} as {kind!r}, not the whole value"]


def test_an_edge_on_bytes_of_no_unit_is_an_extra():
    case = _case()
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    resolved, _, _, _ = resolve_edges(case, [("i1", "request", "i2", "request", "MRN", "123", "123", "whole", None, "o1", None)],
                                      leg_of_key)
    assert resolved == {("?123", 1, 2, None)}


def test_two_origins_of_one_value_are_two_rows_by_right_and_one_origin_twice_is_red():
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    row = ("i1", "request", "i2", "request", "MRN", MRN, MRN, "whole", None)
    resolved, _, _, problems = resolve_edges(_case(), [(*row, "o1", None), (*row, "o2", None)], leg_of_key)
    assert resolved == {("s1", 1, 2, None)} and problems == []
    _, _, _, problems = resolve_edges(_case(), [(*row, "o1", None), (*row, "o1", None)], leg_of_key)
    assert problems == ["edge s1 1>2 (MRN) drawn by 2 rows"]


def _record(version, legs, enforcement, tier=0, late=False):
    return {"version": version, "legs": legs, "enforcement_type": enforcement, "tier": tier, "late": late}


@pytest.mark.parametrize("records, role, verdict, problem", [
    ([_record(1, ["request"], "block")], "request", "block", None),
    ([_record(1, ["request"], "allow"), _record(2, ["request", "response"], "allow")], "request", "allow", None),
    ([_record(1, ["response"], "block")], "request", None, "no risk record evidences the flagged request leg"),
    ([_record(1, ["request"], "allow"), _record(2, ["request", "response"], "block")], "request",
     ("allow", "block"), None),
    ([_record(1, ["request"], "allow"), _record(2, ["request"], "allow"), _record(3, ["request"], "block"),
      _record(4, ["response"], "allow")], "request", ("allow", "allow", "block"), None),
])
def test_the_verdict_is_the_decision_history_of_the_records_that_evidence_the_flagged_leg(records, role, verdict,
                                                                                          problem):
    _, got, why = verdict_at(records, role)
    assert (got, why) == (verdict, problem)


def test_a_stated_history_shows_every_late_finding_and_a_named_tier_on_every_version():
    records = [_record(1, ["request"], "allow", 1), _record(2, ["request"], "block", 2, True)]
    assert verdict_at(records, "request", listed=True)[1] == ("allow", "block late")
    assert verdict_at(records, "request", listed=True, tiers=True)[1] == ("allow tier 1", "block tier 2 late")
    assert verdict_at(records[:1], "request", listed=True, tiers=True)[1] == ("allow tier 1",)   # one version
    assert verdict_at(records[:1], "request")[1] == "allow"          # a single verdict states no tier


def test_a_late_finding_the_history_does_not_state_is_red():
    case = _case(verdict={"off": "allow", "conv": "allow", "cross": ("allow", "block")})
    out = _judge(case, verdicts={"off": "allow", "conv": "allow", "cross": ("allow", "block late")})
    assert out.status == "RED" and "verdict cross: want ('allow', 'block'), got ('allow', 'block late')" in out.reasons


def test_a_single_verdict_stated_against_a_changed_decision_is_red():
    out = _judge(_case(), verdicts={"cross": ("allow", "block")})
    assert out.status == "RED" and "verdict cross: want block, got ('allow', 'block')" in out.reasons


def test_a_decision_history_without_tiers_is_not_held():
    case = _case(verdict={"off": "allow", "conv": "allow", "cross": ("allow", "block")})
    assert "tiers" not in case.uses
    runs = {"off": "allow", "conv": "allow"}
    assert _judge(case, verdicts={**runs, "cross": ("allow", "block")}).status == "GREEN"
    assert _judge(case, verdicts={**runs, "cross": ("allow", "block", "allow")}).status == "RED"


def test_a_stated_history_is_every_version_repeats_included():
    case = _case(verdict={"off": "allow", "conv": "allow", "cross": ("block", "allow")})
    _, got, _ = verdict_at([_record(1, ["request"], "block"), _record(2, ["request"], "allow"),
                            _record(3, ["request"], "allow")], "request")
    out = _judge(case, verdicts={"off": "allow", "conv": "allow", "cross": got})
    assert out.status == "RED" and "verdict cross: want ('block', 'allow'), got ('block', 'allow', 'allow')" in out.reasons


def test_reclassify_takes_a_unit_only_where_the_premise_labels_it(tmp_path: Path):
    body = ("steps: [{send: '1-6'}, drain, {reclassify: {%s: [s1]}}, drain]\nexpect:\n  edges: []\n"
            "classifier:\n  must_see: {1: [s1]}\n  must_miss: {2: [s1], 3: [s1]}\n")
    with pytest.raises(ValueError, match="reclassify takes a unit only from a leg the premise"):
        load_case(_yaml(tmp_path, body % 2, line="U>A[s1 -], A>T1[s1 -], T1>V1[s1 -], V1>T1[-], T1>A[-], A>U[-]"))


# --- the case states everything; the renderer refuses an ambiguous line ------------------------------------------

def test_edges_are_required(tmp_path: Path):
    with pytest.raises(ValueError, match="expect.edges is required"):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT))


def test_an_unknown_expect_key_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match=re.escape("unknown expect keys ['also']")):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n  also: []\n"))


def test_two_units_never_travel_as_the_same_bytes():
    with pytest.raises(ValueError, match="would travel as the same bytes 'Rosa Alvarez'"):
        render("Y", {"s1": "NAME", "s2": "PN"}, cl.parse("U>A[s1 s2@who -]"))


@pytest.mark.parametrize("line, error", [
    ("U>A[s1 s1 -]", "two values under the key 'mrn'"),
    ("U>A[s1@id s2@id -]", "two values under the key 'id'"),
    ("U>A[s1@note -]", "key 'note' is reserved"),
    ("U>A[s1@records -]", "key 'records' is reserved"),
    ("U>A[{s1 s2@mrn}]", "two values under the key 'mrn'"),
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
    grid = render_grid(_case(), _runs(set(), dict.fromkeys(MODES, "allow")))
    expected, observed = grid.split("\n\nobserved\n")
    assert expected.startswith("expected\n")
    assert "━" in expected and "━" not in observed.split("verdict")[0]
    assert grid.splitlines()[-1] == ("verdict at 3: expected off=allow / conv=block / cross=block, "
                                     "observed off=allow / conv=allow / cross=allow")


def test_a_line_flags_one_leg_only(tmp_path: Path):
    with pytest.raises(ValueError, match="more than one leg is flagged"):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n",
                        line="U>A[s1 -], A>T1[s1 -]⚑, T1>V1[s1 -]⚑"))


def test_flagged_line_needs_a_verdict(tmp_path: Path):
    with pytest.raises(ValueError, match="needs every mode"):
        load_case(_yaml(tmp_path, "expect:\n  edges: []\n"))
    with pytest.raises(ValueError, match="needs every mode"):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {'off': allow, conv: block}\n  edges: []\n"))
    with pytest.raises(ValueError, match="no leg is flagged"):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n", line="U>A[s1 -]"))


def test_verdict_keys_must_be_quoted(tmp_path: Path):
    with pytest.raises(ValueError, match=re.escape("(quote 'off'), got ['False']")):
        load_case(_yaml(tmp_path, "expect:\n  verdict: {off: allow, conv: block, cross: block}\n  edges: []\n"))


def test_the_case_record_carries_no_timing():
    from .report import case_markdown
    runs = _runs(EDGES)
    runs["cross"].timings = {"reset": 0.1, "total": 0.1}
    record = case_markdown(judge(_case(), runs))
    assert "0.1" not in record and "Risk observer calls: 0" in record


def test_the_case_record_shows_the_observed_marks():
    from .report import case_markdown
    runs = _runs(EDGES)
    runs["cross"].marks = {3: frozenset({"capped 4"})}
    assert "Marks (cross run): leg 3 [capped 4]" in case_markdown(judge(_case(), runs))


# --- every remaining refusal ----------------------------------------------------------------------------------

SEE_ALL = "classifier:\n  must_see: {1: [s1], 2: [s1], 3: [s1]}\n"
VERDICT = "  verdict: {'off': allow, conv: block, cross: block}\n"


@pytest.mark.parametrize("expect, error", [
    ("  edges: [s2 1>2]\n", "naming an undeclared unit or a leg the line lacks"),
    ("  edges: [s1 1>9]\n", "naming an undeclared unit or a leg the line lacks"),
])
def test_the_loader_refuses_an_inconsistent_expectation(tmp_path: Path, expect, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + expect + SEE_ALL))


def test_an_expected_edge_joins_legs_that_carry_its_unit(tmp_path: Path):
    line = "U>A[s1 -], A>T1[-], T1>V1[s1 -]⚑"
    with pytest.raises(ValueError, match=re.escape("legs that do not both carry the unit: s1 1>2")):
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
    ("expect:\n" + VERDICT + "  edges: [s1 2>2]\n" + SEE_ALL, "edges that do not run forward"),
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


@pytest.mark.parametrize("gap, seconds", [("~>", 600), ("~45s>", 45), ("~10min>", 600), ("~2h>", 7200)])
def test_a_later_conversation_starts_after_its_gap_ten_minutes_by_default(gap, seconds):
    line = cl.parse(f"U>A[s1 -], A>U[-] {gap} U>A[-], A>U[-]")
    assert [c.gap_seconds for c in line.conversations] == [0, seconds]


def test_a_tool_may_answer_one_caller_while_serving_another():
    line = ("U>A1[-], U>A2[-], A1>T1[-], A2>T1[-], T1(A2)>V2[-], T1>A1[s1 -], V2>T1[-], T1>A2[-], A1>U[-], A2>U[-]")
    assert len(render("Y", {"s1": "MRN"}, cl.parse(line)).rendered) == 10


# --- the notation across conversations: levels, records, scopes, steps, evidence, marks, histories --------------

def test_records_render_as_an_array_of_objects_beside_the_rest():
    r = render("Y", {"s1": "SSN", "s2": "MRN", "s3": "SSN", "s4": "MRN"},
               cl.parse("U>A[{s1 s2} {s3 s4@id -} -], A>U[-]"))
    body = r.rendered[0].content
    assert body["records"] == [{"ssn": "123-45-6789", "mrn": "4471820"},
                               {"ssn": "987-65-4321", "id": "5530917", "note": body["records"][1]["note"]}]
    assert set(body) == {"records", "note"}
    assert cl.parse("U>A[{s1} s2]").legs[0].elements[1].record is None


@pytest.mark.parametrize("line, error", [
    ("U>A[{s1 {s2}}]", "a record inside a record"),
    ("U>A[{s1 s2]", "a record never closed"),
    ("U>A[s1}]", "'}' closes no record"),
    ("U>A[{} s1]", "an empty element"),
])
def test_the_line_refuses_a_record_it_cannot_read(line, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        cl.parse(line)


@pytest.mark.parametrize("expect, error", [
    ("  edges: [s1 1>2 vo]\n", "expect.edges has a crossing (s1 1>2 vo); a level belongs in expect.cross"),
    ("  edges: [s1 1>2]\n  cross: [s1 1>3 co, s1 1>2 vo]\n", "an edge in both expect.edges and expect.cross"),
    ("  edges: []\n  cross: [s1 1>3]\n", "expect.cross has no crossing"),
    ("  edges: []\n  cross: [s1 1>3 strong]\n", "bad edge 's1 1>3 strong'"),
    ("  edges: []\n  evidence: {3: {s1: [crossed]}}\n", "each once of"),
    ("  edges: []\n  evidence: {3: {s1: [inherited, inherited]}}\n", "each once of"),
    ("  edges: []\n  evidence: {3: {s1: [classified SSN]}}\n", "s1 classified as ['SSN'], not one of its types"),
    ("  edges: []\n  evidence: {3: {s1: [inherited]}}\n", "classified there exactly when classifier.must_see says so"),
    ("  edges: []\n  evidence: {4: {s1: []}}\n", "expect.evidence at leg 4: s1 is not on that leg"),
    ("  edges: []\n  evidence: {9: {s1: []}}\n", "names leg 9, which the line lacks"),
    ("  edges: []\n  marks: {3: [capped none]}\n", "expect.marks at leg 3"),
    ("  edges: []\n  marks: {3: [incomplete time]}\n", "expect.marks at leg 3"),
    ("  edges: []\n  marks: {9: [capped 4]}\n", "expect.marks at leg 9"),
    ("  edges: []\n  marks: {3: [capped 4], '3': [capped 4]}\n", "expect.marks names leg 3 twice"),
    ("  edges: []\n  marks: {3: [capped 4, capped 4]}\n", "expect.marks lists a mark twice at leg 3"),
])
def test_the_loader_refuses_a_level_evidence_or_mark_it_cannot_read(tmp_path: Path, expect, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, "expect:\n" + VERDICT + expect + SEE_ALL))


@pytest.mark.parametrize("top, expect, line, error", [
    ("", "  evidence: {1: {s2: [inherited]}}\n", "U>A[s1 s2 -], A>T1[s1 -], T1>V1[s1 -]⚑, V1>T1[-], T1>A[-], A>U[-]",
     "Y: expect.evidence at leg 1: s2 is not declared in units"),
    ("", "  marks: {3: }\n", LINE, "Y: expect.marks at leg 3: a list of marks, got None"),
    ("scopes: [V1]\n", "", LINE, "Y: scopes maps a party on the line"),
    ("steps:\n  send: 1-6\n", "", LINE, "Y: steps is a list of drain, send, derive, reclassify and cross steps"),
])
def test_the_loader_names_the_case_for_a_shape_it_cannot_read(tmp_path: Path, top, expect, line, error):
    see = SEE_ALL + ("  must_miss: {1: [s2]}\n" if "s2" in line else "")
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, top + "expect:\n" + VERDICT + "  edges: []\n" + expect + see, line=line))


def test_the_example_in_the_loader_docstring_loads(tmp_path: Path):
    from . import cases
    text = cases.__doc__.split("every key shown:\n\n", 1)[1].split("\n\n", 1)[0]
    (tmp_path / "X1").mkdir()
    (tmp_path / "X1" / "case.yaml").write_text("\n".join(line[4:] for line in text.splitlines()) + "\n")
    case = load_case(tmp_path / "X1")
    assert case.cross == {("s1", 3, 10, "vo"), ("s1", 10, 11, None), ("s1", 11, 12, None)}
    assert case.uses == {"scopes", "config"} and case.pending.missing == {("s1", 3, 10, "vo")}


def test_the_loader_reads_a_level_evidence_marks_and_a_history(tmp_path: Path):
    case = load_case(_yaml(tmp_path, "expect:\n  verdict: {'off': [allow tier 0], conv: allow, cross: "
                                     "[allow tier 1, block tier 2]}\n  edges: [s1 1>2]\n  cross: [s1 1>3 co]\n"
                                     "  evidence: {3: {s1: [classified MRN, crossed co ambiguous]}}\n"
                                     "  marks: {3: [capped 4, incomplete payload_bytes]}\n" + SEE_ALL))
    assert case.cross == {("s1", 1, 3, "co")} and case.edges == {("s1", 1, 2, None)}
    assert case.evidence == {3: {"s1": frozenset({"classified MRN", "crossed co ambiguous"})}}
    assert case.marks == {3: frozenset({"capped 4", "incomplete payload_bytes"})}
    assert case.verdict["cross"] == ("allow tier 1", "block tier 2") and case.uses == {"tiers"}


@pytest.mark.parametrize("verdict, error", [
    ("{'off': [], conv: allow, cross: allow}", "a decision history lists"),
    ("{'off': [allow at 1], conv: allow, cross: allow}", "a decision history lists"),
    ("{'off': [allow tier 3], conv: allow, cross: allow}", "a decision history lists"),
    ("{'off': [late], conv: allow, cross: allow}", "a decision history lists"),          # late is no decision
    ("{'off': [allow tier 1, block late], conv: allow, cross: allow}", "states it on every version"),
    ("{'off': [allow tier 0], conv: [allow], cross: allow}", "states it on every version"),
])
def test_a_decision_history_names_each_tier(tmp_path: Path, verdict, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, f"expect:\n  verdict: {verdict}\n  edges: []\n" + SEE_ALL))


@pytest.mark.parametrize("top, error", [
    ("scopes: {V9: call}\n", "scopes maps a party on the line"),
    ("scopes: {V1: forever}\n", "scopes maps a party on the line"),
    ("config: {retention: 3}\n", "config sets one of"),
    ("config: {payload_bytes: -1}\n", "config sets one of"),
    ("config: {payload_bytes: true}\n", "config sets one of"),
])
def test_the_loader_refuses_a_scope_or_a_setting_it_does_not_know(tmp_path: Path, top, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, top + "expect:\n" + VERDICT + "  edges: []\n" + SEE_ALL))


def test_steps_default_to_every_leg_then_the_drains_and_read_ranges_both_ways(tmp_path: Path):
    assert load_case(_yaml(tmp_path, "expect:\n" + VERDICT + "  edges: []\n" + SEE_ALL)).steps == (
        ("send", (1, 2, 3, 4, 5, 6)), ("drain",))
    steps = "steps: [{send: 4-6}, drain, {derive: 1}, {cross: 'on'}, {send: '3-1'}, drain]\n"
    case = load_case(_yaml(tmp_path, steps + "expect:\n" + VERDICT + "  edges: []\n" + SEE_ALL))
    assert case.steps == (("send", (4, 5, 6)), ("drain",), ("derive", 1), ("cross", "on"), ("send", (3, 2, 1)),
                          ("drain",))
    assert case.uses == {"switch"}
    line = "U>A[-], A>T1[-], ..V1.., T1>A[s1 -], A>U[-]"   # leg 3 is the unseen hop: a range steps over it
    case = load_case(_yaml(tmp_path, "steps: [{send: 1-6}, drain]\nexpect:\n  edges: []\nclassifier:\n"
                                     "  must_see: {5: [s1]}\n", line=line))
    assert case.steps[0] == ("send", (1, 2, 5, 6))


@pytest.mark.parametrize("steps, error", [
    ("[{send: 1-3}, drain]", "steps send every leg exactly once: twice [], never [4, 5, 6]"),
    ("[{send: 1-6}, {send: 6}, drain]", "twice [6], never []"),
    ("[drain, {send: 1-6}]", "the steps end with work no drain follows"),
    ("[{send: 1-6}, drain, {derive: 1}]", "the steps end with work no drain follows"),
    ("[{send: 1-6}, drain, {derive: 2}, drain]", "derive names a conversation 1..1"),
    ("[{send: 1-6}, {wait: 1}, drain]", "a step is drain, or one of send"),
    ("[{send: 1-6}, sleep]", "a step is drain, or one of send"),
    ("[{send: 'one'}, drain]", "send takes leg indices and ranges"),
    ("[{send: '1-6, 9'}, drain]", "send names leg 9, which puts nothing on the wire"),
    ("[{cross: on}, {send: 1-6}, drain]", "the cross step is 'on' or 'off' (quoted)"),
    ("[{send: 1-6}, {reclassify: {4: [s1]}}, drain]", "reclassify maps a leg to units it carries"),
])
def test_the_loader_refuses_a_missing_or_unreadable_step(tmp_path: Path, steps, error):
    with pytest.raises(ValueError, match=re.escape(error)):
        load_case(_yaml(tmp_path, f"steps: {steps}\nexpect:\n" + VERDICT + "  edges: []\n" + SEE_ALL))


def test_a_step_sends_its_legs_rows_in_the_order_given():
    r = render("Y", {"s1": "MRN"}, cl.parse(LINE))
    by_leg = {i: [row for row, leg in zip(r.rows, r.row_legs) if leg == i] for i in range(1, 7)}
    assert r.rows_of((3, 1)) == by_leg[3] + by_leg[1] and len(r.rows_of(tuple(range(1, 7)))) == len(r.rows)
    assert r.trace_of(1) == r.rendered[0].trace_id


def test_a_duplicate_row_is_red():
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2}
    row = ("i1", "request", "i2", "request", "MRN", MRN, MRN, "whole", None, "o1", None)
    resolved, _, _, problems = resolve_edges(_case(), [row, row], leg_of_key)
    assert resolved == {("s1", 1, 2, None)} and problems == ["edge s1 1>2 (MRN) drawn by 2 rows"]
    assert _judge(_case(), problems=problems).status == "RED"
    other_type = ("i1", "request", "i2", "request", "OPID", MRN, MRN, "whole", None, "o2", None)   # a second origin, by right
    case = _case(accepts={"s1": frozenset({"MRN", "OPID"})})
    assert resolve_edges(case, [row, other_type], leg_of_key)[3] == []
    crossing = ("i1", "request", "i2", "request", "MRN", MRN, MRN, "whole", "vo", "o3", None)      # beside the conversation's
    assert resolve_edges(_case(), [row, crossing], leg_of_key)[0] == {("s1", 1, 2, None), ("s1", 1, 2, "vo")}


def test_an_extra_crossed_origin_hidden_by_the_edge_set_turns_the_evidence_ambiguous_and_red():
    """The edge set keeps no origin, so a second origin of a crossing with the same carrier and level is invisible
    there; but a value with several crossed origins is ambiguous at that leg (R3, the derivation marks every edge of
    them, ``tests/label_lineage/test_cross.py``), and a case that states the one origin states no ambiguity."""
    from .engine import evidence_at
    leg_of_key = {("i1", "request"): 1, ("i2", "request"): 2, ("i3", "request"): 3}
    case = _case(edges=set(), cross={("s1", 2, 3, "vo")}, evidence={3: {"s1": frozenset({"crossed vo"})}})
    crossing = ("i2", "request", "i3", "request", "MRN", MRN, MRN, "whole", "vo")

    def judged(rows):
        resolved, _, inherited, problems = resolve_edges(case, rows, leg_of_key)
        legs = {i: _at(i) for i in (1, 2, 3)}
        for i, edges in inherited.items():
            legs[i].inherited = edges
        return resolved, _judge(case, cross_edges=resolved, problems=problems, evidence=evidence_at(case, legs))

    resolved, out = judged([(*crossing, "o1", "crossed vo")])
    assert resolved == {("s1", 2, 3, "vo")} and out.status == "GREEN"
    resolved, out = judged([(*crossing, "o1", "crossed vo ambiguous"), (*crossing, "o2", "crossed vo ambiguous")])
    assert resolved == {("s1", 2, 3, "vo")}                          # the edge set alone hides the second origin
    assert out.status == "RED" and out.reasons == ["evidence at 3 s1: want [crossed vo], got [crossed vo ambiguous]"]


def test_evidence_is_read_from_the_rows_never_from_a_path():
    from .engine import evidence_at
    case = _case(units={"s1": "MRN", "s2": "SSN"})
    leg = _at(3, ("MRN", MRN), ("OPID", MRN), ("SSN", "000-00-0000"))
    leg.inherited = [{"unit": "s1", "level": None, "known": "inherited"},
                     {"unit": "s1", "level": "vo", "known": "crossed vo"},
                     {"unit": "s2", "level": None, "known": "crossed co ambiguous"}]
    assert evidence_at(case, {3: leg, 4: _at(4)}) == {
        3: {"s1": {"classified MRN", "classified OPID", "inherited", "crossed vo"}, "s2": {"crossed co ambiguous"}},
        4: {}}


def test_a_finding_classifies_a_unit_only_as_its_whole_value():
    from .engine import evidence_at
    legs = {3: _at(3, ("MRN", MRN + "9"), ("OPID", "ref " + MRN)), 4: _at(4, ("MRN", MRN[:-1]))}
    assert evidence_at(_case(), legs) == {3: {}, 4: {}}


def test_every_shipped_case_agrees_with_its_calibration():
    """The classifier premise and the types the evidence states (R8), against the findings every run replays: read
    from the files, so a case held from running is checked too."""
    from .classify import recorded_findings
    from .cases import all_cases
    from .report import premise
    wrong = []
    for case in all_cases():
        found = recorded_findings(case, render(case.id, case.units, case.line))
        legs = {i: _at(i, *f) for i, f in found.items()}
        wrong += [f"{case.id}: {n}" for n in premise(case, _obs("cross", set(), None, legs),
                                                      (case.must_see, case.must_miss))]
        for i, cells in case.evidence.items():
            for unit, kinds in cells.items():
                got = {f"classified {t}" for t, text in found[i] if text == case.values[unit]}
                if got != {k for k in kinds if k.startswith("classified")}:
                    wrong.append(f"{case.id}: leg {i} {unit} states {sorted(kinds)}, the model gave {sorted(got)}")
    assert wrong == []


def test_a_reclassified_cell_must_end_unlabelled_and_the_calibration_keeps_the_first_premise():
    from .report import premise
    case = _case(must_see={1: ["s1"]}, steps=(("send", (1,)), ("drain",), ("reclassify", ((1, ("s1",)),)),
                                               ("drain",)))
    value = case.values["s1"]
    assert case.final_premise == ({}, {1: ["s1"]})
    labelled, unlabelled = (_obs("cross", set(), None, {1: _at(1, *f)}) for f in ([("MRN", value)], []))
    assert premise(case, unlabelled) == []
    assert premise(case, labelled) == ["leg 1: the model DID label s1 (MRN); the case assumed it would not"]
    assert premise(case, labelled, (case.must_see, case.must_miss)) == []
