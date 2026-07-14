"""Fixtures for the `travel_agent_III` trace tests.

`travel_agent_III.json` is the canonical live travel-advisor trace
(`8ae1f64d4bb51b750168c6ef1e11a2d8`) referenced throughout ADR-0007 and the
openai_agents v1.4.1 span reference, captured verbatim from the deployment's
`spans` table (ADR-0006: the row IS the Span). A single travel-advisor agent
(OpenAI Agents SDK, fronted by an A2A server) calls one LLM and three tools
(`search_destinations`, `get_weather`, `get_flights`).

It is the observed-agent, tool-using counterpart to the other travel-advisor
traces: `travel_agent_I` (single clarifying turn, no tool) and `travel_agent_II`
(4-agent cross-framework delegation). Unique to this trace among the suite: the
two `get_flights` calls ERRORED — the only error signal across all the trace
fixtures.

Five entities: one observed agent + one inferred LLM + three inferred tools.

The span-row loader lives in the parent package's conftest; we reuse it here so
the row→Span mapping stays a single source of truth (ADR-0006). `CANONICAL_TRACE_ID`
also stays in the parent conftest as the shared ADR-0007 reference constant.
"""

from __future__ import annotations

import pytest

from data_governance.retrieval import Span

# Reuse the parent package's loader — same fixtures/ dir, same row mapping.
from ..conftest import load_trace_spans

CANONICAL_TRACE_ID = "8ae1f64d4bb51b750168c6ef1e11a2d8"


@pytest.fixture()
def canonical_trace_spans() -> list[Span]:
    """Spans of the canonical travel-advisor trace (ADR-0007 examples)."""
    return load_trace_spans("travel_agent_III")
