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


# A hand-built "split graph" trace exercising the features implemented for
# ADR-0007's deferred clauses. A `weather-agent` makes one
# `openinference.instrumentation.anthropic` LLM call whose:
#   * INPUT messages replay a prior `calendar` tool call (a genuinely-new
#     input-side tool, never seen as an output) → inferred + ordered BEFORE
#     the LLM interaction (Step 2.b case 3 input side / ordering rule 3);
#   * OUTPUT messages ask for `get_weather` → inferred + ordered AFTER the LLM
#     interaction (ordering rule 4).
# Separately, the `get_weather` tool is *observed* executing as its own span
# (`weather-tool` service) in a DIFFERENT White/Gray component (broken
# traceparent — a split graph). Step 2.c folds the inferred `tool:get_weather`
# peer into that observed `weather-tool` entity. The trace therefore exercises,
# in one fixture: input-side tool inference (rule 3), explicit interaction
# ordering (F6a), the Step 2.c inferred↔observed merge, and Step 3.b naming
# (entities named from service.name rather than 'unknown').
INFERRED_OBSERVED_MERGE_TRACE_ID = "b17047c0000000000000000000000001"


@pytest.fixture()
def inferred_observed_merge_trace_spans() -> list[Span]:
    """Spans of the split-graph trace exercising 2.c / F6a / F6b / 3.b."""
    return load_trace_spans("trace_inferred_observed_merge")


# A real `openinference.instrumentation.anthropic` trace captured verbatim from
# the deployment's `spans` table (the trace the in-pod CLI runs are checked
# against). A `patent-assistant` agent makes three `messages.create` LLM calls
# across three turns; each turn's LLM output asks for a tool (`database`, then
# `file`), and following turns replay the prior tool calls on their INPUT
# messages. This three-turn shape exercises the Step-4 merge ordering/timing
# fixes more fully than the hand-built two-turn `trace_anthropic_tool_calls`:
#   * the replayed `database` / `file` calls (same args / tool_call.id across
#     output + later inputs) collapse to one interaction each (edge merge);
#   * each merged tool call keeps its *originating* (output) order, sorting
#     AFTER its turn's LLM rather than ahead of it;
#   * the three agent↔LLM turns keep three DISTINCT started_at values (timing
#     follows the anchor span, not a pooled min, and the response anchors on the
#     observed endpoint, not the merged peer's first-turn span).
ANTHROPIC_LIVE_TRACE_ID = "8e8d7b1ee84bd8995e3c951f659292a2"


@pytest.fixture()
def anthropic_live_trace_spans() -> list[Span]:
    """Spans of the live 3-turn patent-assistant anthropic trace."""
    return load_trace_spans("trace_8e8d7b1e")


# A second real `openinference.instrumentation.anthropic` trace captured
# verbatim from the deployment's `spans` table. A `patent_search` agent makes
# three `messages.create` LLM calls; two of the turns' outputs each ask for a
# *different* tool (`file`, then `web_search`), and each tool is invoked exactly
# once. Unlike `trace_8e8d7b1e` (where the same tool call is replayed on a later
# turn's INPUT messages and the Step-4 edge merge collapses the replay), here no
# call repeats — so every output-derived tool interaction stays in its positive
# (after-LLM) band with no merge. It is the clean multi-tool, output-only
# counterpart to the replay-heavy `trace_8e8d7b1e`: same 4-entity / 10-interaction
# shape (one observed agent + three inferred peers: the LLM and two tools), but
# the tool calls are distinct rather than merged.
ANTHROPIC_MULTITOOL_TRACE_ID = "4ee0239356d61584bb4c3b6965041788"


@pytest.fixture()
def anthropic_multitool_trace_spans() -> list[Span]:
    """Spans of the live patent_search anthropic trace (file + web_search)."""
    return load_trace_spans("trace_4ee02393")
