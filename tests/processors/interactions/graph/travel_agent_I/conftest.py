"""Fixtures for the `travel_agent_I` trace tests.

`travel_agent_I.json` is a real single-agent travel-advisor trace
(`186b5703acde0532adc6940e9eda3cb1`) captured verbatim from the deployment's
`spans` table (ADR-0006: the row IS the Span). It is a *single clarifying turn*:
the `travel-advisor` agent (OpenAI Agents SDK, fronted by an A2A server) makes
exactly ONE LLM call and then asks the user for more detail — so no tool is ever
invoked. The three `mcp_tools` spans present are framework-internal tool
*discovery* (`kagenti.node.type=framework-internal`), not calls.

Unlike the anthropic `patent_agent_*` traces (bare-leaf, where the agent is
*inferred*), openai_agents emits a proper agent/run span, so here the agent is
*observed*. The LLM is remote and emits no spans, so it is a one-sided
observation stubbed as an inferred peer. Two entities total:

  * agent:travel-advisor            (observed)
  * llm:claude-haiku-4-5-20251001   (inferred — remote, one-sided)

This is the smallest real trace in the suite: one observed agent, one inferred
LLM, one bidirectional call. It is the single-turn counterpart to the 4-agent
`travel_agent_II` delegation trace.

The span-row loader lives in the parent package's conftest; we reuse it here so
the row→Span mapping stays a single source of truth (ADR-0006).
"""

from __future__ import annotations

import pytest

from data_governance.retrieval import Span

# Reuse the parent package's loader — same fixtures/ dir, same row mapping.
from ..conftest import load_trace_spans

CLARIFYING_TURN_TRACE_ID = "186b5703acde0532adc6940e9eda3cb1"


@pytest.fixture()
def clarifying_turn_trace_spans() -> list[Span]:
    """Spans of the single-LLM-call travel-advisor trace (no tool invoked)."""
    return load_trace_spans("travel_agent_I")
