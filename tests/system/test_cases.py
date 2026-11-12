"""One test per case: the case through the real system, with inherited labels on
and off, judged against the case's expectations; GREEN and PENDING pass, RED fails. Each
run writes the case's ``result.md``; its timings go to the test output. Then the runs
leg-ready did not finish, which are red (they write no ``result.md``)."""

from __future__ import annotations

import psycopg
import pytest

from data_governance.processors.leg_ready.driver import LegDeferred
from data_governance.risk.engine.opa import OpaRequestError
from tests.harness.fixtures import OtlpHarness

from . import engine, report
from .cases import all_cases

CASES = all_cases()


@pytest.mark.opa
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case(case, otlp_harness: OtlpHarness, trunk_opa) -> None:
    on = engine.run_case(case, dsn=otlp_harness.dsn, grpc_endpoint=otlp_harness.grpc_endpoint,
                         opa_client=trunk_opa, mode="on")
    off = engine.run_case(case, dsn=otlp_harness.dsn, grpc_endpoint=otlp_harness.grpc_endpoint,
                          opa_client=trunk_opa, mode="off")
    outcome = report.judge(case, on, off)
    report.write(outcome)
    print("\n" + report.case_markdown(outcome))
    for obs in (on, off):
        print(f"timing ({obs.mode}): " + ", ".join(f"{k} {v}" for k, v in obs.timings.items()))
    assert outcome.status != "RED", outcome.reasons


@pytest.mark.opa
@pytest.mark.parametrize("case_id", ["C1", "C4"])
def test_a_run_leg_ready_did_not_finish_is_red(case_id, otlp_harness: OtlpHarness, trunk_opa, monkeypatch) -> None:
    """The risk observer defers the last leg as it does when OPA is unreachable, so leg-ready stops one leg short.
    The last leg is always a response leg (a request leg is inserted before its response), so this holds in any
    order the legs are derived in. Every other leg is delivered, the
    flagged exchange's record and every edge are in place, so the rows alone pass; the run is red because leg-ready
    did not finish. With inherited labels on, a recompute request may wait behind the held leg; off keeps label-only
    requests by design."""
    make = engine.make_risk_observer

    def deferring_the_last_leg(opa):
        observe = make(opa)
        with psycopg.connect(otlp_harness.dsn) as conn:
            (last,) = conn.execute("SELECT max(seq) FROM interaction_legs").fetchone()

        def observer(leg):
            if leg.seq == last:
                raise LegDeferred("OPA unreachable") from OpaRequestError("injected outage")
            return observe(leg)
        return observer

    monkeypatch.setattr(engine, "make_risk_observer", deferring_the_last_leg)
    case = next(c for c in CASES if c.id == case_id)
    obs = {mode: engine.run_case(case, dsn=otlp_harness.dsn, grpc_endpoint=otlp_harness.grpc_endpoint,
                                 opa_client=trunk_opa, mode=mode)
           for mode in ("on", "off")}
    outcome = report.judge(case, obs["on"], obs["off"])
    print(outcome.reasons)
    assert outcome.status == "RED"
    held = "incomplete run: leg_ready stopped at cursor 15 of 16"
    on, off = ([p for p in obs[m].problems if p.startswith("incomplete run:")] for m in ("on", "off"))
    assert on[0] == held and all(p.endswith("row(s) in risk_recompute_requests") for p in on[1:]), on
    assert off == [held]
    assert (obs["on"].verdict, obs["off"].verdict) == (case.verdict["on"], case.verdict["off"])   # the rows alone pass
    assert obs["on"].edges == obs["off"].edges == case.edges


@pytest.mark.opa
def test_label_only_recompute_requests_are_work_with_inherited_labels_on_only(otlp_harness: OtlpHarness,
                                                                               trunk_opa) -> None:
    """C4 with inherited labels off finishes and leaves label-only recompute requests for a later `on` run; read
    with labels on, the same rows are work left undone."""
    case = next(c for c in CASES if c.id == "C4")
    obs = engine.run_case(case, dsn=otlp_harness.dsn, grpc_endpoint=otlp_harness.grpc_endpoint,
                          opa_client=trunk_opa, mode="off")
    assert obs.problems == []
    assert engine.backlog(otlp_harness.dsn, "off")[1]["leg_ready"] == 0
    assert engine.backlog(otlp_harness.dsn, "on")[1]["leg_ready"] > 0
