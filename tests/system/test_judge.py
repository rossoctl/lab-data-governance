"""The judge without containers: the branches no shipped case exercises."""

from __future__ import annotations

from pathlib import Path

import pytest

from . import caseline as cl
from .cases import Case, load_case
from .engine import Observation
from .report import judge

LINE = "U>A[s1 -], A>T1[s1 -], T1>V1[s1 -]⚑"


def _case(**over) -> Case:
    base = dict(id="J", title="", cell="", line_text=LINE, line=cl.parse(LINE), units={"s1": "MRN"},
                verdict={"on": "block", "off": "allow"}, edges={("s1", 1, 2), ("s1", 2, 3)}, cannot=None,
                pending=None, must_see={}, must_miss={}, authored=False, notes="", dir=Path("."))
    base.update(over)
    return Case(**base)


def _obs(mode: str, edges: set, verdict: str) -> Observation:
    return Observation(mode=mode, legs={}, edges=edges, raw_edges=[], verdict=verdict, timings={},
                       neighbourhood={}, opa_calls=0)


def test_green_when_edges_and_verdicts_match():
    out = judge(_case(), _obs("on", {("s1", 1, 2), ("s1", 2, 3)}, "block"), _obs("off", set(), "allow"))
    assert out.status == "GREEN" and not out.reasons


def test_pending_case_that_passes_is_red():
    out = judge(_case(pending="waits"), _obs("on", {("s1", 1, 2), ("s1", 2, 3)}, "block"), _obs("off", set(), "allow"))
    assert out.status == "RED" and out.reasons[0].startswith("passes while marked pending")


def test_pending_case_that_fails_is_pending():
    out = judge(_case(pending="waits"), _obs("on", {("s1", 1, 2)}, "allow"), _obs("off", set(), "allow"))
    assert out.status == "PENDING" and out.reasons[0] == "pending: waits"


def test_extra_edge_is_red_unless_from_an_earlier_holding():
    extra = judge(_case(), _obs("on", {("s1", 1, 2), ("s1", 2, 3), ("s1", 1, 3)}, "block"), _obs("off", set(), "allow"))
    assert extra.status == "GREEN" and extra.origin_edges == {("s1", 1, 3)}   # 1>3 beside the expected 2>3
    unrelated = judge(_case(), _obs("on", {("s1", 1, 2), ("s1", 2, 3), ("s1", 3, 1)}, "block"), _obs("off", set(), "allow"))
    assert unrelated.status == "RED" and any(r.startswith("edges extra") for r in unrelated.reasons)


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
