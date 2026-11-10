"""One test per case: the case through the real system, with inherited labels on
and off, judged against the case's expectations. RED fails; RED-BY-DESIGN and
PENDING pass and are reported as such; the report is written at session end."""

from __future__ import annotations

import pytest

from tests.harness.fixtures import OtlpHarness

from . import engine, report
from .cases import all_cases

CASES = all_cases()


@pytest.mark.opa
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case(case, otlp_harness: OtlpHarness, trunk_opa, results: report.Results) -> None:
    on = engine.run_case(case, dsn=otlp_harness.dsn, grpc_endpoint=otlp_harness.grpc_endpoint,
                         opa_client=trunk_opa, mode="on")
    off = engine.run_case(case, dsn=otlp_harness.dsn, grpc_endpoint=otlp_harness.grpc_endpoint,
                          opa_client=trunk_opa, mode="off")
    outcome = report.judge(case, on, off)
    results.outcomes.append(outcome)
    print("\n" + report.case_markdown(outcome))
    assert outcome.status != "RED", outcome.reasons
