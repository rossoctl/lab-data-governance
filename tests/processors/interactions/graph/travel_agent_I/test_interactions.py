"""Interactions extracted from the real single-turn travel-advisor trace.

Companion to `test_entities.py` (the entity-set stage). This pins the *edges* of
the P-interactions graph for trace `186b5703acde0532adc6940e9eda3cb1`: the single
agent↔LLM call, with direction, count, error signal, evidence, and order.

Per ADR-0026 Step 3.b the one call site becomes a *pair* of edges —
source→target (request) and target→source (response) — so the extractor emits
exactly two ProtoInteractions. We assert on the directed (caller_key, callee_key)
pair counts (entity identity is the `natural_key`, the stable ADR-0026 signal).

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
# One bidirectional call: the agent calls the LLM once, the LLM returns once.
EXPECTED_DIRECTED_PAIRS = {
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"): 1,
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"): 1,
}

EXPECTED_INTERACTION_COUNT = 2

# The full ordered interaction sequence, sorted by `order` alone (the ADR-0026
# consumer contract: the CLI sorts `key=lambda r: r.order`, the API `ORDER BY
# "order"`). Each entry is (caller_key, callee_key).
#
# GROUND TRUTH — HUMAN-VALIDATED. DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN.
# The single turn is request then response: the agent calls the LLM, the LLM
# returns. It is the oracle, not a snapshot; a mismatch means the ordering
# REGRESSED — fix the code.
EXPECTED_ORDER = [
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),   # call LLM
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),   # LLM returns
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


def test_interaction_pairs_and_counts(clarifying_turn_trace_spans):
    """Directed call pairs and their counts match the human-validated ground truth."""
    result = extract(clarifying_turn_trace_spans)

    assert len(result.interactions) == EXPECTED_INTERACTION_COUNT
    assert _directed_pairs(result) == EXPECTED_DIRECTED_PAIRS


def test_interactions_are_bidirectional(clarifying_turn_trace_spans):
    """The call site appears as both caller->peer and peer->caller.

    ADR-0026 Step 3.b forms one request + one response leg per call site, so the
    forward and return counts for the pair must match.
    """
    result = extract(clarifying_turn_trace_spans)
    pairs = _directed_pairs(result)

    for (caller, callee), n in pairs.items():
        assert pairs.get((callee, caller)) == n, (
            f"{caller} -> {callee} ({n}) has no matching return edge"
        )


def test_no_errored_interactions(clarifying_turn_trace_spans):
    """No interaction carries an error signal in this trace (ground truth)."""
    result = extract(clarifying_turn_trace_spans)

    n_error = sum(1 for ix in result.interactions if ix.error)
    assert n_error == 0


def test_interactions_have_payloads_and_evidence(clarifying_turn_trace_spans):
    """Every interaction carries request+response payloads and a span anchor."""
    result = extract(clarifying_turn_trace_spans)

    for ix in result.interactions:
        assert ix.request_payload_hash is not None
        assert ix.response_payload_hash is not None

    # One evidence span per interaction, and it is the anchor.
    assert len(result.interaction_spans) == len(result.interactions)
    assert all(s.is_anchor for s in result.interaction_spans)


def test_interaction_order_is_exact_sequence(clarifying_turn_trace_spans):
    """The interactions, sorted by `order` alone, match the human-validated walk.

    Pins the exact 0..1 execution-order sequence (ADR-0026 Step 3.b): consumers
    sort by `order` only, so this is what the CLI/API/UI render. It also asserts
    `order` is a dense, unique 0..N-1 global ordinal (no gaps, no ties).
    """
    result = extract(clarifying_turn_trace_spans)

    by_id = {e.id: e.natural_key for e in result.entities}
    ordered = sorted(result.interactions, key=lambda ix: ix.order)

    # order is a dense, unique global ordinal.
    assert [ix.order for ix in ordered] == list(range(len(ordered)))

    assert [
        (by_id[ix.caller_entity_id], by_id[ix.callee_entity_id]) for ix in ordered
    ] == EXPECTED_ORDER
