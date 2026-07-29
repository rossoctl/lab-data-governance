"""Fixtures for the `patent_agent_II` trace tests.

`patent_agent_II.json` is a real single-agent `openinference.instrumentation.`
`anthropic` trace (`4ee0239356d61584bb4c3b6965041788`) captured verbatim from the
deployment's `spans` table (ADR-0006: the row IS the Span). A `patent_search`
agent makes three `messages.create` LLM calls; two of the turns' outputs each ask
for a *different* tool (`file`, then `web_search`), and each tool is invoked
exactly once.

Like `patent_agent_I` this is the **anthropic bare-leaf** case (ADR-0026 Step 2.c
case 4): the framework emits only bare leaf LLM spans sharing a single transport
(`POST /`) parent, no run/agent/wrapper span, so the agent is *inferred*. Every
entity is inferred — the agent (case 4), the remote LLM, and the two tools. Four
entities total:

  * agent:patent_search             (inferred — case-4 bare-leaf agent)
  * llm:claude-haiku-4-5-20251001   (inferred — remote, one-sided)
  * tool:file                       (inferred — one-sided)
  * tool:web_search                 (inferred — one-sided)

The one difference from `patent_agent_I` (`8e8d7b1e`): there the same tool call is
replayed on a later turn's INPUT messages and the Step 2.d edge merge collapses
the replay. Here NO call repeats — each tool is evidenced as an LLM *output*
exactly once — so every output-derived tool interaction stays in its positive
(after-LLM) band with no merge. Same 4-entity / 10-interaction shape, but the two
tool calls are distinct rather than merged.

The span-row loader lives in the parent package's conftest; we reuse it here so
the row→Span mapping stays a single source of truth (ADR-0006).
"""

from __future__ import annotations

import pytest

from data_governance.retrieval import Span

# Reuse the parent package's loader — same fixtures/ dir, same row mapping.
from ..conftest import load_trace_spans

PATENT_SEARCH_TRACE_ID = "4ee0239356d61584bb4c3b6965041788"


@pytest.fixture()
def patent_search_trace_spans() -> list[Span]:
    """Spans of the live 3-turn patent_search anthropic trace (file + web_search)."""
    return load_trace_spans("patent_agent_II")
