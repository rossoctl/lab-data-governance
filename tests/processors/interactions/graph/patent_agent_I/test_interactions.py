"""Interactions extracted from the real single-agent anthropic patent-assistant trace.

Companion to `test_entities.py` (the entity-set stage). This pins the *edges* of
the P-interactions graph for trace `8e8d7b1ee84bd8995e3c951f659292a2`: the calls
between the entities, with direction, count, error signal, and evidence.

Per ADR-0025 Step 3.b each call site becomes a *pair* of edges — source→target
(request) and target→source (response) — so the agent and each peer are joined
in both directions, and the extractor turns every edge into one ProtoInteraction.
We assert on the directed (caller_key -> callee_key) pair counts (entity identity
is the `natural_key`, the stable ADR-0025 signal).

The three agent→LLM turns stay three distinct interactions (three distinct
`started_at` — timing follows the anchor span, not a pooled min). The `database`
and `file` tool calls each appear once as an LLM *output* and are then *replayed*
on later turns' *input* messages; the Step 2.d edge merge collapses each replay
so the tool is a SINGLE call (one interaction each), keeping its originating
(output) order — sorting AFTER its turn's LLM, not ahead of it.

GROUND TRUTH — DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN. The interaction set
below was reviewed and confirmed correct by a human against the real trace. It is
the confirmed-correct expected output for this trace, not merely a snapshot of
what the extractor currently emits, so a failure here means the output REGRESSED
and the code should be fixed to match these expectations (not the other way
around).
"""

from __future__ import annotations

from data_governance.processors.interactions.graph.extractor import extract

# Expected directed call pairs: (caller_key, callee_key) -> count.
#
# GROUND TRUTH — DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN. 6 directed pairs,
# 10 interactions total. Every call site is bidirectional: each caller->peer
# pair has a matching peer->caller pair with the same count (asserted below).
# The agent calls the LLM 3x (once per turn) and each tool 1x (the replayed
# calls merge to one).
EXPECTED_DIRECTED_PAIRS = {
    ("agent:patent-assistant", "llm:claude-haiku-4-5-20251001"): 3,
    ("agent:patent-assistant", "tool:database"): 1,
    ("agent:patent-assistant", "tool:file"): 1,
    ("llm:claude-haiku-4-5-20251001", "agent:patent-assistant"): 3,
    ("tool:database", "agent:patent-assistant"): 1,
    ("tool:file", "agent:patent-assistant"): 1,
}

EXPECTED_INTERACTION_COUNT = 10

# The full ordered interaction sequence, sorted by `order` alone (the ADR-0025
# consumer contract: the CLI sorts `key=lambda r: r.order`, the API `ORDER BY
# "order"`). Each entry is (caller_key, callee_key). Read top-to-bottom this is
# the execution order the UI renders.
#
# GROUND TRUTH — DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN. This exact sequence
# was reviewed and confirmed correct by a human against the real trace: three
# agent↔LLM turns, and after the turns whose LLM output asks for a tool the
# agent↔tool call interleaves immediately (leaf tool returns right after its
# request — ADR-0025 Step 3.b recursive execution-order walk). It is the oracle,
# not a snapshot; a mismatch means the ordering REGRESSED — fix the code.
EXPECTED_ORDER = [
    ("agent:patent-assistant", "llm:claude-haiku-4-5-20251001"),   # turn 1: call LLM
    ("llm:claude-haiku-4-5-20251001", "agent:patent-assistant"),   #         LLM returns
    ("agent:patent-assistant", "tool:database"),                   # turn 1 output → database
    ("tool:database", "agent:patent-assistant"),                   #         database returns
    ("agent:patent-assistant", "llm:claude-haiku-4-5-20251001"),   # turn 2: call LLM
    ("llm:claude-haiku-4-5-20251001", "agent:patent-assistant"),   #         LLM returns
    ("agent:patent-assistant", "tool:file"),                       # turn 2 output → file
    ("tool:file", "agent:patent-assistant"),                       #         file returns
    ("agent:patent-assistant", "llm:claude-haiku-4-5-20251001"),   # turn 3: call LLM
    ("llm:claude-haiku-4-5-20251001", "agent:patent-assistant"),   #         LLM returns
]


def _directed_pairs(result):
    """{(caller_key, callee_key): count} over all interactions."""
    by_id = {e.id: e for e in result.entities}
    counts: dict[tuple[str, str], int] = {}
    for ix in result.interactions:
        caller = by_id[ix.caller_entity_id].natural_key
        callee = by_id[ix.callee_entity_id].natural_key
        counts[(caller, callee)] = counts.get((caller, callee), 0) + 1
    return counts


def test_interaction_pairs_and_counts(patent_agent_trace_spans):
    """Directed call pairs and their counts match the human-validated ground truth."""
    result = extract(patent_agent_trace_spans)

    assert len(result.interactions) == EXPECTED_INTERACTION_COUNT
    assert _directed_pairs(result) == EXPECTED_DIRECTED_PAIRS


def test_interactions_are_bidirectional(patent_agent_trace_spans):
    """Every call site appears as both caller->peer and peer->caller.

    ADR-0025 Step 3.b forms one request + one response leg per call site, so the
    forward and return counts for any pair must match.
    """
    result = extract(patent_agent_trace_spans)
    pairs = _directed_pairs(result)

    for (caller, callee), n in pairs.items():
        assert pairs.get((callee, caller)) == n, (
            f"{caller} -> {callee} ({n}) has no matching return edge"
        )


def test_no_errored_interactions(patent_agent_trace_spans):
    """No interaction carries an error signal in this trace (ground truth)."""
    result = extract(patent_agent_trace_spans)

    n_error = sum(1 for ix in result.interactions if ix.error)
    assert n_error == 0


def test_interactions_have_payloads_and_evidence(patent_agent_trace_spans):
    """Every interaction carries request+response payloads and a span anchor."""
    result = extract(patent_agent_trace_spans)

    for ix in result.interactions:
        assert ix.request_payload_hash is not None
        assert ix.response_payload_hash is not None

    # One evidence span per interaction, and it is the anchor.
    assert len(result.interaction_spans) == len(result.interactions)
    assert all(s.is_anchor for s in result.interaction_spans)


def test_interaction_order_is_exact_sequence(patent_agent_trace_spans):
    """The interactions, sorted by `order` alone, match the human-validated walk.

    Pins the exact 0..9 execution-order sequence (ADR-0025 Step 3.b): consumers
    sort by `order` only, so this is what the CLI/API/UI render. It also asserts
    `order` is a dense, unique 0..N-1 global ordinal (no gaps, no ties).
    """
    result = extract(patent_agent_trace_spans)

    by_id = {e.id: e.natural_key for e in result.entities}
    ordered = sorted(result.interactions, key=lambda ix: ix.order)

    # order is a dense, unique global ordinal.
    assert [ix.order for ix in ordered] == list(range(len(ordered)))

    assert [
        (by_id[ix.caller_entity_id], by_id[ix.callee_entity_id]) for ix in ordered
    ] == EXPECTED_ORDER


def test_three_llm_turns_have_distinct_start_times(patent_agent_trace_spans):
    """The three agent→LLM turns are three distinct interactions in time.

    The replayed tool calls merge (Step 2.d), but the three LLM turns must NOT:
    each anchors on its own `messages.create` span, so the three call legs carry
    three distinct `started_at` values (timing follows the anchor span, not a
    pooled min over merged spans).
    """
    result = extract(patent_agent_trace_spans)

    by_id = {e.id: e.natural_key for e in result.entities}
    llm_calls = [
        ix
        for ix in result.interactions
        if by_id[ix.caller_entity_id] == "agent:patent-assistant"
        and by_id[ix.callee_entity_id] == "llm:claude-haiku-4-5-20251001"
    ]
    assert len(llm_calls) == 3
    assert len({ix.started_at for ix in llm_calls}) == 3
