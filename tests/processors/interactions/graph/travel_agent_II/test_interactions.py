"""Interactions extracted from the real 4-agent cross-framework travel-advisor trace.

Companion to `test_entities.py` (the entity-set stage). This pins the *edges* of
the P-interactions graph for trace `e8f7f7c4d7b35e5aa4fbdaaae2a90f75`: the calls
between the entities, with direction, count, error signal, and evidence.

Per ADR-0025 each call site becomes a *pair* of Black edges — source→target
(request) and target→source (response) — so a caller and each peer are joined in
both directions, and the extractor turns every Black edge into one
ProtoInteraction. We assert on the directed (caller_key -> callee_key) pair
counts (entity identity is the `natural_key`, the stable ADR-0025 signal).

The travel-advisor's two delegations are `agent → agent` interactions, NOT calls
to `tool:delegate_to_*` peers: per ADR-0025 Step 2.d rule 4 each delegation call
site is the shared root of an inferred callee chain and an observed transport
chain reaching the downstream agent, so the observed agent wins. They appear as
`agent:travel-advisor → agent:research-agent` and
`agent:travel-advisor → agent:booking_agent` (with their response legs).

The interaction set below has been human-validated — as have the entities in
`test_entities.py`. It is the confirmed-correct expected output for this trace,
not merely a snapshot of what the extractor currently emits, so a failure here
means the output REGRESSED and the code should be fixed to match these
expectations (not the other way around).

Human comment: The trace spans are not correctly generated — the underlying
trace has incorrect traceparent structure (e.g. `execute_tool` parented under
`call_llm`), so some interaction ordering reflects that malformed input. The
expectations below are validated as the correct output GIVEN this trace as-is;
they are not a claim that the trace itself is well-formed.
"""

from __future__ import annotations

from data_governance.processors.interactions.graph.extractor import extract

# Expected directed call pairs: (caller_key, callee_key) -> count.
#
# GROUND TRUTH (unvalidated snapshot — see module docstring). 24 directed pairs,
# 50 interactions total. Every call site is bidirectional: each caller->peer
# pair has a matching peer->caller pair with the same count (asserted below).
# The per-caller call counts (travel-advisor calls the LLM 7x, booking_agent 4x,
# etc.) are the shape currently produced from the trace's spans.
#
# The two delegations are `agent → agent` (ADR-0025 Step 2.d rule 4), not calls
# to `tool:delegate_to_*` peers: travel-advisor → research-agent (1x) and
# travel-advisor → booking_agent (2x, booking is invoked twice), each with a
# matching response leg.
EXPECTED_DIRECTED_PAIRS = {
    ("agent:booking_agent", "agent:travel-advisor"): 2,
    ("agent:booking_agent", "llm:claude-haiku-4-5-20251001"): 4,
    ("agent:booking_agent", "tool:create_booking"): 2,
    ("agent:payment-agent", "llm:claude-haiku-4-5-20251001"): 3,
    ("agent:payment-agent", "tool:charge_card"): 1,
    ("agent:payment-agent", "tool:get_payment_info"): 1,
    ("agent:research-agent", "agent:travel-advisor"): 1,
    ("agent:research-agent", "llm:claude-haiku-4-5-20251001"): 1,
    ("agent:travel-advisor", "agent:booking_agent"): 2,
    ("agent:travel-advisor", "agent:research-agent"): 1,
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"): 7,
    ("agent:travel-advisor", "tool:get_flights"): 1,
    ("agent:travel-advisor", "tool:get_weather"): 1,
    ("agent:travel-advisor", "tool:search_destinations"): 1,
    ("llm:claude-haiku-4-5-20251001", "agent:booking_agent"): 4,
    ("llm:claude-haiku-4-5-20251001", "agent:payment-agent"): 3,
    ("llm:claude-haiku-4-5-20251001", "agent:research-agent"): 1,
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"): 7,
    ("tool:charge_card", "agent:payment-agent"): 1,
    ("tool:create_booking", "agent:booking_agent"): 2,
    ("tool:get_flights", "agent:travel-advisor"): 1,
    ("tool:get_payment_info", "agent:payment-agent"): 1,
    ("tool:get_weather", "agent:travel-advisor"): 1,
    ("tool:search_destinations", "agent:travel-advisor"): 1,
}

EXPECTED_INTERACTION_COUNT = 50

# The full interaction sequence, sorted by `order` alone (ADR-0025 consumer
# contract: the CLI sorts `key=lambda r: r.order`, the API `ORDER BY "order"`).
# Each entry is (caller_key, callee_key). Read top-to-bottom this is the execution
# order the UI renders.
#
# GROUND TRUTH — HUMAN-VALIDATED. DO NOT EDIT WITHOUT CONFIRMATION BY A HUMAN.
# This exact ordering was reviewed and confirmed correct by a human, including
# the deep-nesting region around order 33–44: the trace nests payment-agent's
# whole subtree (33–42) and `create_booking` (32/43) inside booking_agent's
# `call_llm` span, so that outer LLM call's response leg (44) is emitted last
# when the subtree unwinds — the correct LIFO output GIVEN THIS TRACE AS-IS. The
# trace's traceparent is what produces this sequence (see this module's header
# comment — it has some malformed traceparent, e.g. `execute_tool` parented under
# `call_llm`; the ordering below is the correct output for that input, not a
# claim the trace itself is well-formed). It is the oracle for this trace, not a
# snapshot of transient output — a mismatch means the ordering REGRESSED, so fix
# the code, not this expectation.
EXPECTED_ORDER = [
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "tool:search_destinations"),
    ("tool:search_destinations", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "tool:get_weather"),
    ("tool:get_weather", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "tool:get_flights"),
    ("tool:get_flights", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "agent:research-agent"),
    ("agent:research-agent", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:research-agent"),
    ("agent:research-agent", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "agent:booking_agent"),
    ("agent:booking_agent", "llm:claude-haiku-4-5-20251001"),
    ("agent:booking_agent", "tool:create_booking"),
    ("tool:create_booking", "agent:booking_agent"),
    ("llm:claude-haiku-4-5-20251001", "agent:booking_agent"),
    ("agent:booking_agent", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:booking_agent"),
    ("agent:booking_agent", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:travel-advisor"),
    ("agent:travel-advisor", "agent:booking_agent"),
    ("agent:booking_agent", "llm:claude-haiku-4-5-20251001"),
    ("agent:booking_agent", "tool:create_booking"),
    ("agent:payment-agent", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:payment-agent"),
    ("agent:payment-agent", "tool:get_payment_info"),
    ("tool:get_payment_info", "agent:payment-agent"),
    ("agent:payment-agent", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:payment-agent"),
    ("agent:payment-agent", "tool:charge_card"),
    ("tool:charge_card", "agent:payment-agent"),
    ("agent:payment-agent", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:payment-agent"),
    ("tool:create_booking", "agent:booking_agent"),
    ("llm:claude-haiku-4-5-20251001", "agent:booking_agent"),
    ("agent:booking_agent", "llm:claude-haiku-4-5-20251001"),
    ("llm:claude-haiku-4-5-20251001", "agent:booking_agent"),
    ("agent:booking_agent", "agent:travel-advisor"),
    ("agent:travel-advisor", "llm:claude-haiku-4-5-20251001"),
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


def test_interaction_pairs_and_counts(multi_agent_delegation_trace_spans):
    """Directed call pairs and their counts match the ground-truth snapshot."""
    result = extract(multi_agent_delegation_trace_spans)

    assert len(result.interactions) == EXPECTED_INTERACTION_COUNT
    assert _directed_pairs(result) == EXPECTED_DIRECTED_PAIRS


def test_interactions_are_bidirectional(multi_agent_delegation_trace_spans):
    """Every call site appears as both caller->peer and peer->caller.

    ADR-0025 Step 2.c adds bidirectional Black edges for each call site, so the
    forward and return counts for any pair must match.
    """
    result = extract(multi_agent_delegation_trace_spans)
    pairs = _directed_pairs(result)

    for (caller, callee), n in pairs.items():
        assert pairs.get((callee, caller)) == n, (
            f"{caller} -> {callee} ({n}) has no matching return edge"
        )


def test_no_errored_interactions(multi_agent_delegation_trace_spans):
    """No interaction carries an error signal in this trace (ground truth)."""
    result = extract(multi_agent_delegation_trace_spans)

    n_error = sum(1 for ix in result.interactions if ix.error)
    assert n_error == 0


def test_interactions_have_payloads_and_evidence(multi_agent_delegation_trace_spans):
    """Every interaction carries request+response payloads and a span anchor."""
    result = extract(multi_agent_delegation_trace_spans)

    for ix in result.interactions:
        assert ix.request_payload_hash is not None
        assert ix.response_payload_hash is not None

    # One evidence span per interaction, and it is the anchor.
    assert len(result.interaction_spans) == len(result.interactions)
    assert all(s.is_anchor for s in result.interaction_spans)


def test_delegation_response_leg_anchors_on_callee_span(
    multi_agent_delegation_trace_spans,
):
    """An A2A-delegation response leg anchors on the CALLEE agent's own span, not
    the caller's `delegate_to_*` call-site span (ADR-0025 Step 3.c / Step 2.d
    rule 4).

    Each `agent → agent` delegation is a pair of legs that pool the SAME spans —
    the caller's `delegate_to_*` call-site TOOL span and the callee agent's own
    wrapper span. The timing anchor is direction-aware: the call leg anchors on
    the call-site span, the response leg on the responding (callee) agent's span.
    So the two legs must have DISTINCT anchor spans, the response must start at or
    after the call, and the response anchor must belong to the callee's service —
    never the caller's call-site span.

    Guards the `(n.is_inferred, n.id != from_id, not _has_payload(n))` endpoint
    sort key in `build_entity_graph._anchor_and_payload`: reverting the middle
    `n.id != from_id` term collapses both legs onto the same (call-site) anchor,
    which this test rejects.
    """
    result = extract(multi_agent_delegation_trace_spans)

    natural_key = {e.id: e.natural_key for e in result.entities}
    span_by_id = {s.span_id: s for s in multi_agent_delegation_trace_spans}
    anchor_by_ix = {
        s.interaction_id: s.span_id
        for s in result.interaction_spans
        if s.is_anchor
    }

    # Index interactions by directed (caller_key, callee_key) pair; a pair may
    # occur more than once (booking is delegated to twice), so keep a list.
    by_pair: dict[tuple[str, str], list] = {}
    for ix in result.interactions:
        pair = (
            natural_key[ix.caller_entity_id],
            natural_key[ix.callee_entity_id],
        )
        by_pair.setdefault(pair, []).append(ix)

    # (caller, callee, callee's service_name) — the service names are hyphenated
    # (`research-agent`) where the natural keys may use underscores
    # (`booking_agent`), so pin the expected service explicitly.
    delegations = [
        ("agent:travel-advisor", "agent:research-agent", "research-agent"),
        ("agent:travel-advisor", "agent:booking_agent", "booking-agent"),
    ]

    for caller, callee, callee_service in delegations:
        calls = by_pair[(caller, callee)]
        responses = by_pair[(callee, caller)]
        assert len(calls) == len(responses), (
            f"{caller}↔{callee}: {len(calls)} call legs vs "
            f"{len(responses)} response legs"
        )

        # Pair each call with its response by order (calls sort before responses
        # within a call site); sorting both by started_at aligns them.
        calls = sorted(calls, key=lambda ix: (ix.started_at, ix.order))
        responses = sorted(responses, key=lambda ix: (ix.started_at, ix.order))

        for call_leg, response_leg in zip(calls, responses):
            call_anchor = anchor_by_ix[call_leg.id]
            resp_anchor = anchor_by_ix[response_leg.id]

            # Distinct anchor spans for the two legs.
            assert call_anchor != resp_anchor, (
                f"{caller}↔{callee}: call and response share anchor {call_anchor}"
            )

            # The response does not precede its call (they anchor on different
            # spans, so the times genuinely differ).
            assert response_leg.started_at > call_leg.started_at, (
                f"{caller}↔{callee}: response leg does not start after its call"
            )

            # The response anchor is the CALLEE agent's own span, not the
            # caller's `delegate_to_*` call-site span.
            resp_span = span_by_id[resp_anchor]
            assert resp_span.service_name == callee_service, (
                f"{caller}↔{callee}: response leg anchored on "
                f"{resp_span.service_name!r} span {resp_anchor} "
                f"(expected the callee's {callee_service!r} span)"
            )


def test_order_is_dense_unique_ordinal(multi_agent_delegation_trace_spans):
    """`order` is a dense, unique 0..N-1 global ordinal.

    ADR-0025: consumers sort by `order` alone, so it must be total and unique.
    """
    result = extract(multi_agent_delegation_trace_spans)
    orders = sorted(ix.order for ix in result.interactions)
    assert orders == list(range(len(result.interactions)))


def test_interaction_order_is_exact_sequence(multi_agent_delegation_trace_spans):
    """The interactions, sorted by `order` alone, match the human-validated walk.

    Pins the exact execution-order sequence (ADR-0025 Step 3.b): consumers sort by
    `order` only, so this is what the CLI/API/UI render. This is the correct
    output for this trace given its traceparent as-is (see EXPECTED_ORDER).
    """
    result = extract(multi_agent_delegation_trace_spans)

    by_id = {e.id: e.natural_key for e in result.entities}
    ordered = sorted(result.interactions, key=lambda ix: ix.order)
    assert [
        (by_id[ix.caller_entity_id], by_id[ix.callee_entity_id]) for ix in ordered
    ] == EXPECTED_ORDER
