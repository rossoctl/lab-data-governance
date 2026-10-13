"""Offline guard for the check recorder and the table report (default suite,
no cluster): a synthetic run record renders, exports CSV, and diffs against
a second record by (case, id)."""

import json
from pathlib import Path

import pytest

from tests.live import report
from tests.live.checks import Checks


class _Rec:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, name, obj, *, raw=False):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(obj if raw else json.dumps(obj, default=str), encoding="utf-8")
        return p


def _run(tmp_path: Path, name: str, *, ext_rules, leak) -> Path:
    run = tmp_path / name
    run.mkdir()
    (run / "env.json").write_text(json.dumps({"kube_context": "kind-x", "app_ns": "travel-advisor", "destructive": "True"}))
    (run / "preflight.json").write_text(json.dumps({"dg.commit": {"observed": "abcdef0"}}))
    (run / "capabilities.json").write_text(json.dumps({"alerts": False, "health": False, "ledger": True}))
    with Checks("L7", _Rec(run / "L7"), raise_at_exit=True) as ch:
        ch.check("ext.rules", "card external: triggered rules", ["DG-001", "DG-004"], ext_rules)
        ch.record("trace.version", "trace record version", 30)
        ch.check("risk.entity_set", "entity set equal", "12 entities", "13 entities", ok=False,
                 known="entity set mismatch: stale callee (#279)")
    try:
        with Checks("L6", _Rec(run / "L6"), raise_at_exit=True) as ch:
            ch.check("isolation.leak", "other guest in this trace", [], leak,
                     comment="PII from another session inside this session's hops" if leak else None)
            ch.check("forest.roots", "roots", 1, 1)
    except AssertionError:
        pass
    (run / "verdict.json").write_text(json.dumps({
        "test_L7_known_payloads": {"outcome": "passed", "duration": 12.0, "longrepr": None},
        "test_L6_cross_trace_isolation": {"outcome": "failed" if leak else "passed", "duration": 100.0,
                                          "longrepr": "E   AssertionError: L6 isolation.leak" if leak else None},
        "test_L13_alert_supersession": {"outcome": "skipped", "duration": 0.0, "longrepr": None,
                                        "reason": "no alerts processor on the deployed branch"},
    }))
    return run


def test_deferred_failures_are_raised_together(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="2 expectation"):
        with Checks("X", _Rec(tmp_path), raise_at_exit=True) as ch:
            ch.check("a", "first", 1, 2)
            ch.check("b", "second", "x", "y")
            ch.check("c", "third", True, True)
    rows = json.loads((tmp_path / "checks.json").read_text())
    assert [r["status"] for r in rows] == ["FAIL", "FAIL", "pass"]


def test_known_never_raises_and_reads_known(tmp_path: Path) -> None:
    with Checks("X", _Rec(tmp_path)) as ch:
        ok = ch.check("a", "p", 1, 2, known="filed defect")
    assert ok is False
    rows = json.loads((tmp_path / "checks.json").read_text())
    assert rows[0]["status"] == "KNOWN" and "filed defect" in rows[0]["comment"]


def test_render_csv_and_diff(tmp_path: Path) -> None:
    a = _run(tmp_path, "A", ext_rules=["DG-001", "DG-004"], leak=[["booking-agent", "outbound", "inference", "input"]])
    b = _run(tmp_path, "B", ext_rules=["DG-001", "DG-004"], leak=[])
    md = report.render(a)
    assert "| `ext.rules` |" in md and "**FAIL**" in md and "KNOWN" in md and "recorded" in md
    assert "no alerts processor" in md  # the skip reason reaches the verdict table
    assert "booking-agent cross-session prompt leak" in md  # the finding is recognised from the row's comment
    assert "stale peer.host callee" in md
    csv_text = report.to_csv(a)
    assert csv_text.splitlines()[0] == "case,id,property,expected,actual,status,comment"
    assert sum(1 for _ in csv_text.splitlines()) == 1 + 5
    d = report.diff(a, b)
    assert "`isolation.leak`" in d and "`ext.rules`" not in d  # only the changed row
    assert "1 of 5 rows differ" in d
