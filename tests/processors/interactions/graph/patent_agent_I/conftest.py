"""Fixtures for the `patent_agent_I` trace tests.

`patent_agent_I.json` is a real single-agent `openinference.instrumentation.`
`anthropic` trace (`8e8d7b1ee84bd8995e3c951f659292a2`) captured verbatim from the
deployment's `spans` table (ADR-0006: the row IS the Span) — the same trace the
in-pod CLI runs are checked against. A `patent-assistant` agent makes three
`messages.create` LLM calls across three turns; each turn's LLM output asks for a
tool (`database`, then `file`), and following turns replay the prior tool calls
on their INPUT messages.

This is the **anthropic bare-leaf** case: the framework emits only bare leaf LLM
spans (`messages.create`) sharing a single transport (`POST /`) parent — there is
NO run/agent/wrapper span. So per ADR-0025 Step 2.c case 4 the agent itself is
*inferred* (`infer_agent_from_bare_leaf_llms`). Unlike `travel_agent_II` (four
observed agents), every entity in this trace is inferred: the agent (case 4), the
remote LLM (one-sided, emits no spans), and the two tools (one-sided
observations). Four entities total:

  * agent:patent-assistant          (inferred — case-4 bare-leaf agent)
  * llm:claude-haiku-4-5-20251001   (inferred — remote, one-sided)
  * tool:database                   (inferred — one-sided)
  * tool:file                       (inferred — one-sided)

The replayed `database` / `file` calls (same args / tool_call.id across an
output turn and later turns' inputs) collapse to one interaction each (Step 2.d
edge merge), and each merged tool call keeps its *originating* (output) order —
so it sorts AFTER its turn's LLM, not ahead of it.

The span-row loader lives in the parent package's conftest; we reuse it here so
the row→Span mapping stays a single source of truth (ADR-0006).
"""

from __future__ import annotations

import pytest

from data_governance.retrieval import Span

# Reuse the parent package's loader — same fixtures/ dir, same row mapping.
from ..conftest import load_trace_spans

PATENT_ASSISTANT_TRACE_ID = "8e8d7b1ee84bd8995e3c951f659292a2"


@pytest.fixture()
def patent_agent_trace_spans() -> list[Span]:
    """Spans of the live 3-turn patent-assistant anthropic trace."""
    return load_trace_spans("patent_agent_I")
