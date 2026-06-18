"""Fixtures for the P-interactions graph-prototype tests.

The extractor (`p_interactions_proto.extractor.extract`) is a pure function
over a list of `retrieval.Span` objects — it does not touch the database. So
these tests feed it a captured snapshot of a real trace rather than spinning up
Postgres: the snapshot lives in `fixtures/*.json` and is reconstructed into
`Span` objects here.

`trace_8ae1f64d.json` is the canonical live trace referenced throughout
ADR-0007 and the openai_agents v1.4.1 span reference — a single travel-advisor
agent (OpenAI Agents SDK, fronted by an A2A server) calling one LLM and three
tools. It was captured verbatim from the `spans` table of the data-governance
deployment with:

    SELECT ... FROM spans WHERE trace_id = '8ae1f64d4bb51b750168c6ef1e11a2d8'

so the rows match the `Span` columns 1:1 (ADR-0006: the row *is* the Span).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from data_governance.retrieval import Span

_FIXTURES = Path(__file__).parent / "fixtures"

# The trace every ADR-0007 example is drawn from.
CANONICAL_TRACE_ID = "8ae1f64d4bb51b750168c6ef1e11a2d8"


def _parse_dt(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _row_to_span(row: dict) -> Span:
    """Reconstruct a `Span` from one captured `spans`-table row.

    Timestamps are stored as ISO-8601 strings in the fixture (JSON has no
    datetime); everything else maps straight across.
    """
    return Span(
        seq=row["seq"],
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        parent_id=row["parent_id"],
        name=row["name"],
        started_at=_parse_dt(row["started_at"]),
        attributes=row["attributes"] or {},
        observed_at=_parse_dt(row["observed_at"]),
        arrival_seq=row["arrival_seq"],
        service_name=row.get("service_name"),
        kind=row.get("kind"),
        error=row.get("error"),
        status_message=row.get("status_message"),
        events=row.get("events"),
        links=row.get("links"),
        ended_at=_parse_dt(row.get("ended_at")),
        otlp=row.get("otlp"),
        scope=row.get("scope"),
        resource_attributes=row.get("resource_attributes"),
    )


def load_trace_spans(name: str) -> list[Span]:
    """Load a captured trace fixture (`fixtures/<name>.json`) as `Span`s."""
    rows = json.loads((_FIXTURES / f"{name}.json").read_text())
    return [_row_to_span(r) for r in rows]


@pytest.fixture()
def canonical_trace_spans() -> list[Span]:
    """Spans of the canonical travel-advisor trace (ADR-0007 examples)."""
    return load_trace_spans("trace_8ae1f64d")


# A second travel-advisor trace, captured the same way (verbatim from the
# `spans` table). Unlike the canonical trace it is a *single clarifying turn*:
# the agent makes exactly one LLM call and then asks the user for more detail,
# so no tool is ever invoked. The three `mcp_tools` spans present are
# framework-internal tool *discovery* (kagenti.node.type=framework-internal),
# not calls — so the only entities/interactions are the agent and its LLM.
CLARIFYING_TURN_TRACE_ID = "186b5703acde0532adc6940e9eda3cb1"


@pytest.fixture()
def clarifying_turn_trace_spans() -> list[Span]:
    """Spans of a single-LLM-call travel-advisor trace (no tool invoked)."""
    return load_trace_spans("trace_186b5703")


# A hand-built `claude_agent_sdk` trace exercising ADR-0007 Step 2.b case 2:
# a `ClaudeAgentSDK.query` combined agent→LLM span whose children are
# `ClaudeAgentSDK.{tool_name}` tool/sub-agent dispatches. The dispatched
# targets emit no spans of their own, so they are materialised as inferred
# TARGET peers. Two `get_weather` dispatches + one `book_flight` dispatch
# verify that repeated dispatches of the same target converge to one entity
# (Step 3.a phase 2) while the agent's own dispatch spans stay folded into the
# single agent entity (Step 2.d kind+role-matched edge coloring). Attribute
# shapes follow `openinference_telemetry_spans.md` (claude_agent_sdk 0.1.5).
CLAUDE_SUBAGENT_TRACE_ID = "c1a0de00000000000000000000000001"


@pytest.fixture()
def claude_subagent_trace_spans() -> list[Span]:
    """Spans of a claude_agent_sdk trace with tool/sub-agent dispatches."""
    return load_trace_spans("trace_claude_subagent")
