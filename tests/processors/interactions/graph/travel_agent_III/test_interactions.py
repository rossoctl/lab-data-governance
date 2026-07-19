"""Interactions extracted from the canonical travel-advisor trace.

Companion to `test_entities.py` (the entity-set stage). This pins the *edges* of
the P-interactions graph for trace `8ae1f64d4bb51b750168c6ef1e11a2d8`: the calls
between the entities, with direction, count, error signal, evidence, and order.

Per ADR-0025 Step 3.b each call site becomes a *pair* of edges — source→target
(request) and target→source (response) — so the agent and each peer are joined
in both directions, and the extractor turns every edge into one ProtoInteraction.
We assert on the directed (caller_key, callee_key) pair counts (entity identity
is the `natural_key`, the stable ADR-0025 signal).

Unique to this trace: the two `get_flights` calls ERRORED — the only error
signal across the whole trace-fixture suite. Both directions of each errored call
carry `error=True` (4 errored legs, 14 clean).

GROUND TRUTH — HUMAN-VALIDATED. DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN. The
interaction set below was reviewed and confirmed correct by a human against the
real trace. It is the confirmed-correct expected output, not merely a snapshot of
what the extractor currently emits, so a failure here means the output REGRESSED
and the code should be fixed to match these expectations (not the other way
around).
"""

from __future__ import annotations

from data_governance.processors.interactions.graph.extractor import extract

# Expected directed call pairs: (caller_key, callee_key) -> count.
#
# GROUND TRUTH — HUMAN-VALIDATED. DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN.
# 8 directed pairs, 18 interactions total. The agent calls the LLM 5x and the
# three tools 2 / 1 / 1 times; every call site is bidirectional (matching
# peer->agent pairs, asserted below).
EXPECTED_DIRECTED_PAIRS = {
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"): 5,
    ("agent:travel-advisor", "tool:get_flights"): 2,
    ("agent:travel-advisor", "tool:get_weather"): 1,
    ("agent:travel-advisor", "tool:search_destinations"): 1,
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"): 5,
    ("tool:get_flights", "agent:travel-advisor"): 2,
    ("tool:get_weather", "agent:travel-advisor"): 1,
    ("tool:search_destinations", "agent:travel-advisor"): 1,
}

EXPECTED_INTERACTION_COUNT = 18

# The full ordered interaction sequence, sorted by `order` alone (the ADR-0025
# consumer contract: the CLI sorts `key=lambda r: r.order`, the API `ORDER BY
# "order"`). Each entry is (caller_key, callee_key). Read top-to-bottom this is
# the execution order the UI renders.
#
# GROUND TRUTH — HUMAN-VALIDATED. DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN.
# Five agent↔LLM turns, with each tool call interleaved right after the LLM turn
# whose output requested it (leaf tool returns immediately — ADR-0025 Step 3.b
# recursive execution-order walk). The two `get_flights` calls (orders 10-11 and
# 14-15) are the errored ones. It is the oracle, not a snapshot; a mismatch means
# the ordering REGRESSED — fix the code.
EXPECTED_ORDER = [
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),      # turn 1
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "tool:search_destinations"),           # → search_destinations
    ("tool:search_destinations", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),      # turn 2
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "tool:get_weather"),                   # → get_weather
    ("tool:get_weather", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),      # turn 3
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "tool:get_flights"),                   # → get_flights (ERRORED)
    ("tool:get_flights", "agent:travel-advisor"),                   #   (ERRORED)
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),      # turn 4
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "tool:get_flights"),                   # → get_flights again (ERRORED)
    ("tool:get_flights", "agent:travel-advisor"),                   #   (ERRORED)
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),      # turn 5
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
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


def test_interaction_pairs_and_counts(canonical_trace_spans):
    """Directed call pairs and their counts match the human-validated ground truth."""
    result = extract(canonical_trace_spans)

    assert len(result.interactions) == EXPECTED_INTERACTION_COUNT
    assert _directed_pairs(result) == EXPECTED_DIRECTED_PAIRS


def test_interactions_are_bidirectional(canonical_trace_spans):
    """Every call site appears as both source->peer and peer->source.

    ADR-0025 Step 3.b forms one request + one response leg per call site, so the
    forward and return counts for any pair must match.
    """
    result = extract(canonical_trace_spans)
    pairs = _directed_pairs(result)

    for (caller, callee), n in pairs.items():
        assert pairs.get((callee, caller)) == n, (
            f"{caller} -> {callee} ({n}) has no matching return edge"
        )


def test_get_flights_interactions_errored(canonical_trace_spans):
    """The two get_flights calls errored; every other interaction is clean.

    This is the one error signal in the trace — both directions of the
    get_flights call site carry error=True, all 14 other interactions False.
    """
    result = extract(canonical_trace_spans)
    by_id = {e.id: e for e in result.entities}

    errored = {
        tuple(sorted((by_id[ix.caller_entity_id].natural_key,
                      by_id[ix.callee_entity_id].natural_key)))
        for ix in result.interactions
        if ix.error
    }
    assert errored == {("agent:travel-advisor", "tool:get_flights")}

    n_error = sum(1 for ix in result.interactions if ix.error)
    n_clean = sum(1 for ix in result.interactions if ix.error is False)
    assert (n_error, n_clean) == (4, 14)


def test_interactions_have_payloads_and_evidence(canonical_trace_spans):
    """Every interaction carries request+response payloads and a span anchor."""
    result = extract(canonical_trace_spans)

    for ix in result.interactions:
        assert ix.request_payload_hash is not None
        assert ix.response_payload_hash is not None

    # One evidence span per interaction, and it is the anchor.
    assert len(result.interaction_spans) == len(result.interactions)
    assert all(s.is_anchor for s in result.interaction_spans)

    # Payloads are content-addressed; identical bodies collapse to one row, so
    # the unique count is below 2 x 18.
    assert len(result.payloads) == 17


def test_interaction_order_is_exact_sequence(canonical_trace_spans):
    """The interactions, sorted by `order` alone, match the human-validated walk.

    Pins the exact 0..17 execution-order sequence (ADR-0025 Step 3.b): consumers
    sort by `order` only, so this is what the CLI/API/UI render. It also asserts
    `order` is a dense, unique 0..N-1 global ordinal (no gaps, no ties).
    """
    result = extract(canonical_trace_spans)

    by_id = {e.id: e.natural_key for e in result.entities}
    ordered = sorted(result.interactions, key=lambda ix: ix.order)

    # order is a dense, unique global ordinal.
    assert [ix.order for ix in ordered] == list(range(len(ordered)))

    assert [
        (by_id[ix.caller_entity_id], by_id[ix.callee_entity_id]) for ix in ordered
    ] == EXPECTED_ORDER
