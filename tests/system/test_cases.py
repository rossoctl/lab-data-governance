"""One test per case: the case through the real system, with inherited labels on
and off, judged against the case's expectations; GREEN passes, RED fails. Each
run writes the case's ``result.md``; its timings go to the test output."""

from __future__ import annotations

import pytest

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
    assert outcome.status == "GREEN", outcome.reasons
