"""Fixtures for the `travel_agent_II` trace tests.

`travel_agent_II.json` is a real multi-agent, cross-framework
travel-advisor trace (`e8f7f7c4d7b35e5aa4fbdaaae2a90f75`) captured verbatim from
the deployment's `spans` table (ADR-0006: the row IS the Span). Unlike the other
P-interactions fixtures — single-agent (canonical `travel_agent_III`) or
hand-built — this one has FOUR observed agents that delegate to one another
across THREE instrumentation scopes, bridged by observed a2a / httpx / starlette
transport:

  * agent:travel-advisor  (openinference.instrumentation.openai_agents)
  * agent:research-agent  (openinference.instrumentation.langchain)
  * agent:booking_agent   (openinference.instrumentation.google_adk)
  * agent:payment-agent

Each agent's tool calls are one-sided observations (only the caller emitted
spans), so Step 2.c stubs inferred peers for them. The two `delegate_to_*` call
sites travel-advisor uses to reach the sub-agents do NOT become `tool:` peers:
per ADR-0007 Step 2.d rule 4 each is the shared root of an inferred callee chain
and an observed transport chain reaching the downstream agent, so the observed
agent wins (they surface as `agent → agent` interactions, not `tool:delegate_*`
entities). The LLM calls across the agents converge on a single inferred
`llm:claude-haiku-4-5-20251001` peer. Eleven entities total: 4 observed agents,
6 inferred `tool:` peers (`search_destinations`, `get_weather`, `get_flights`,
`create_booking`, `get_payment_info`, `charge_card`), and 1 inferred `llm:` peer.

The span-row loader lives in the parent package's conftest; we reuse it here so
the row→Span mapping stays a single source of truth (ADR-0006).
"""

from __future__ import annotations

import pytest

from data_governance.retrieval import Span

# Reuse the parent package's loader — same fixtures/ dir, same row mapping.
from ..conftest import load_trace_spans

MULTI_AGENT_DELEGATION_TRACE_ID = "e8f7f7c4d7b35e5aa4fbdaaae2a90f75"


@pytest.fixture()
def multi_agent_delegation_trace_spans() -> list[Span]:
    """Spans of the 4-agent cross-framework travel-advisor delegation trace."""
    return load_trace_spans("travel_agent_II")
