"""Trace traversal: which op applies to which leg, and with what inputs (#117).

This is ADR-0027 D1/D2/D4 — inbound routing, memory, and op selection — over one
trace's interaction legs. :func:`derive_trace_lineage` is **pure**: legs and
entities in as plain values, per-leg **data lineage** out. The database is
:mod:`.driver`'s job; the metadata construction is :mod:`.operations`'.

**The traversal unit is the interaction LEG, in leg ``seq`` order.** The spec's
worked example (``docs/data_lineage_alg.md:140-153``) numbers arrows::

    -1-> Agent
         Agent -2-> LLM
         Agent <-3- LLM
         Agent -4-> LLM
         Agent <-5- LLM
    <-6- Agent

and in the ADR-0025 schema each arrow is one leg: #1/#6 are the request/response
legs of the user→agent interaction, #2/#3 of the first agent→llm call, #4/#5 of
the second. So "each interaction ``i`` in ``seq`` order" (D4) resolves to each leg
in leg-``seq`` order — which is also the only ordering the schema offers, since the
parent ``interactions`` row deliberately has no ``seq`` (ADR-0025).

**A leg's producing entity** is the caller for a request leg and the callee for a
response leg — D1's routing rule read backwards. That entity is the one that
performed the transformation, so it is the one ``init`` roots at and the one the
entity path is extended with.

**Inbound routing is purely structural (D1).** A payload is inbound to entity E
iff, in a leg with lower ``seq``, E is the callee and the payload is the request,
OR E is the caller and the payload is the response. No span-level heuristics, no
attribute sniffing — just the leg table.

**Memory folds into the inbound set (D2).** Priors pool per *memory node*
(:mod:`.memory`): an accumulating entity keeps every prior routed to it; a
memoryless one keeps only the latest. That is why selection is on ``|inbound|``
alone and needs no memory predicate of its own — and why the spec's #5 is
``linear`` (the LLM forgot its first turn) while #4 is ``merge`` (the agent did
not).

NOTE on naming: ``processors/interactions`` uses "lineage" for **span** lineage (a
span's ancestors ∪ subtree under a seq horizon — ADR-0007/0016). This module is
**data lineage**: where a payload's content came from. Unrelated concepts.
"""

from __future__ import annotations

import dataclasses
import enum

from data_governance.matching import Matcher

from . import memory, operations
from .operations import DataLineage, Payload

# How a leg is identified everywhere in the traversal and in the persisted table:
# ``(interaction_id, leg_type)`` — ADR-0027 D5's key, and the grain at which a
# lineage fact is unique.
LegKey = tuple[str, str]


class Operation(enum.StrEnum):
    """Which of the three ops the traversal selected for a leg (ADR-0027 D4).

    Recorded on the result so the selection is observable — the persisted table
    stores the metadata, not the op, so this is how a test (or a debug read) sees
    *why* a row looks the way it does. Note ``INIT`` here means the STRUCTURAL init
    of D3(1) (selection); a ``LINEAR``/``MERGE`` whose matcher refused still
    produces init-*shaped* metadata (D3(2)), which is a runtime result rather than
    a selection outcome.
    """

    INIT = "init"
    LINEAR = "linear"
    MERGE = "merge"


@dataclasses.dataclass(frozen=True, slots=True)
class Entity:
    """One participant of the trace, as the traversal needs it: the id legs
    reference, the natural key lineage names it by (spec rule 1: "the data source
    is assigned the entity name"), and the ``kind`` the accumulating predicate
    reads (:mod:`.memory`)."""

    id: str
    natural_key: str
    kind: str


@dataclasses.dataclass(frozen=True, slots=True)
class Leg:
    """One ``interaction_legs`` row, joined to its parent's caller/callee.

    ``interaction_legs`` carries no identity of its own (ADR-0025 puts
    caller/callee on the parent ``interactions`` row) and no ``trace_id``, so the
    driver joins both in before handing legs here — keeping the traversal free of
    SQL and of the parent/leg split.
    """

    interaction_id: str
    leg_type: str  # 'request' | 'response'
    seq: int
    caller_entity_id: str
    callee_entity_id: str
    payload_hash: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class LegLineage:
    """The derived lineage of one leg, plus the derivation itself.

    ``operation`` and ``inbound_payloads`` are not persisted — they exist so the
    selection and its inputs are assertable (the spec's worked example is a claim
    about *which ops ran with which inputs*, which the metadata alone cannot show).
    """

    lineage: DataLineage
    operation: Operation
    inbound_payloads: tuple[Payload, ...]


def _producer_id(leg: Leg) -> str:
    """The entity that PRODUCED this leg's payload.

    D1's routing rule read backwards: a request is produced by the caller and
    consumed by the callee; a response is produced by the callee and consumed by
    the caller.
    """
    return leg.caller_entity_id if leg.leg_type == "request" else leg.callee_entity_id


def _consumer_id(leg: Leg) -> str:
    """The entity this leg's payload is INBOUND TO (D1): requests are inbound to
    the callee, responses are inbound to the caller."""
    return leg.callee_entity_id if leg.leg_type == "request" else leg.caller_entity_id


def derive_trace_lineage(
    legs: list[Leg],
    entities: dict[str, Entity],
    *,
    matcher: Matcher,
    payloads: dict[str, Payload] | None = None,
) -> dict[LegKey, LegLineage]:
    """Derive the data lineage of every leg of one trace.

    *legs* need not be sorted — this sorts by ``seq`` itself, so a caller's row
    order cannot change the answer. *entities* maps ``entity_id -> Entity``.
    *matcher* is the resolved semantic matcher (the caller gets it from
    :func:`data_governance.matching.get_matcher`; this function never names an
    implementation).

    *payloads* maps ``payload_hash -> payload`` and is what the matcher actually
    receives — the driver resolves the stored ``content`` into it so a real,
    content-reading matcher sees content rather than a hash. It is optional because
    the *default* matcher reads neither argument (``simple_match``), so lineage is
    computable from structure alone; when omitted, or for a hash the map does not
    cover, the hash itself stands in. The matching contract permits this — a payload
    is opaque to it, "whatever the caller holds" — and it keeps the traversal
    testable without a payload store.

    Returns ``{(interaction_id, leg_type): LegLineage}`` — the ADR-0027 D5 key.

    Op selection, per leg in ``seq`` order (D4)::

        |inbound| == 0  -> init_lineage(entity)                  # D3(1) structural
        |inbound| == 1  -> linear_lineage(p, out, entity)        # may degrade, D3(2)
        |inbound| >= 2  -> merge_lineage(*inbound, out, entity)  # per-source match

    A leg is skipped (no row) when its producing entity is unknown or its payload
    is absent. Both are eventual-consistency realities of a derived table, and
    neither may fabricate lineage: an unknown producer has no name to root at, and
    an absent payload has nothing for the matcher to compare — routing it as
    inbound anyway would make ``|inbound|`` lie about how many payloads reached the
    entity. Absent-payload handling proper (the trace-level ``partial`` flag,
    ADR-0027 D6) is a separate ticket; this ticket assumes a payload per leg.
    """
    ordered = sorted(legs, key=lambda leg: (leg.seq, leg.interaction_id, leg.leg_type))

    # Metadata of each already-derived LEG, keyed `(interaction_id, leg_type)` — the
    # input side of the next op.
    #
    # Keyed by the leg (the POSITION), never by ``payload_hash``, for the same reason
    # ADR-0027 D5 keys the table that way: payloads are content-addressed and
    # deduped, so identical bytes appear at different positions with completely
    # different lineage, and a hash key collides them. This is not hypothetical — in
    # the captured ``patent_agent_II`` trace an LLM's response leg and the tool leg
    # inferred from that response's ``tool_calls`` carry the SAME hash, so keying
    # here by hash silently merged two distinct priors into one and demoted the
    # agent's ``merge`` to a ``linear``.
    lineage_of_leg: dict[LegKey, DataLineage] = {}
    # Retained inbound legs per memory node, in arrival order (D2). An accumulating
    # node's list grows; a memoryless node's is truncated to its last.
    retained: dict[memory.MemoryNode, list[LegKey]] = {}
    # The payload each leg carries, resolved to content where the caller supplied it.
    # The ops take payloads (the spec's signatures are over payloads, and a real
    # matcher needs content) while routing and metadata lookup key by leg — this map
    # is the bridge between the two.
    resolve = payloads or {}
    payload_of_leg: dict[LegKey, Payload] = {
        (leg.interaction_id, leg.leg_type): resolve.get(
            leg.payload_hash, leg.payload_hash
        )
        for leg in ordered
    }
    result: dict[LegKey, LegLineage] = {}

    for leg in ordered:
        producer = entities.get(_producer_id(leg))
        if producer is not None and leg.payload_hash is not None:
            result[(leg.interaction_id, leg.leg_type)] = _derive_leg(
                leg, producer, retained, lineage_of_leg, payload_of_leg, matcher
            )
        # Route this leg's payload to its consumer's memory AFTER deriving, so a leg
        # never sees itself (D1 is "an interaction with LOWER sequence").
        _route_inbound(leg, entities, retained)

    return result


def _derive_leg(
    leg: Leg,
    producer: Entity,
    retained: dict[memory.MemoryNode, list[LegKey]],
    lineage_of_leg: dict[LegKey, DataLineage],
    payload_of_leg: dict[LegKey, Payload],
    matcher: Matcher,
) -> LegLineage:
    """Select and run the op for one leg, recording its metadata so downstream legs
    can inherit it."""
    key = (leg.interaction_id, leg.leg_type)
    node = memory.memory_node_for_leg(producer, leg)
    # Only priors whose own lineage was derived can be inherited from (a prior whose
    # producer was unknown, or whose payload was absent, has no metadata to give).
    inbound_keys = tuple(k for k in retained.get(node, ()) if k in lineage_of_leg)
    # The matcher receives PAYLOADS, not leg keys — the op signatures the spec
    # defines are over payloads. Two distinct priors may hand it the same payload;
    # that is a real property of the data (content-addressed payloads dedupe), and
    # the matcher is free to answer identically for both.
    inbound = tuple(payload_of_leg[k] for k in inbound_keys)
    output_payload = payload_of_leg[key]

    if not inbound_keys:
        # D3(1) structural init: nothing reached this entity, so its output
        # originates here (a genuine trace root — user input, or an entity whose
        # inbound payload was not lineage-bearing).
        operation = Operation.INIT
        lineage = operations.init_lineage(producer.natural_key)
    elif len(inbound_keys) == 1:
        operation = Operation.LINEAR
        lineage = operations.linear_lineage(
            inbound[0],
            lineage_of_leg[inbound_keys[0]],
            output_payload,
            producer.natural_key,
            matcher=matcher,
        )
    else:
        operation = Operation.MERGE
        lineage = operations.merge_lineage(
            [(payload_of_leg[k], lineage_of_leg[k]) for k in inbound_keys],
            output_payload,
            producer.natural_key,
            matcher=matcher,
        )

    lineage_of_leg[key] = lineage
    return LegLineage(lineage=lineage, operation=operation, inbound_payloads=inbound)


def _route_inbound(
    leg: Leg,
    entities: dict[str, Entity],
    retained: dict[memory.MemoryNode, list[LegKey]],
) -> None:
    """Deliver *leg*'s payload into its consumer's memory node (D1 + D2).

    An accumulating consumer appends (every prior is retained — transient memory is
    always present); a memoryless one replaces (it sees exactly one input, which is
    what keeps it on ``linear``).

    Legs are appended by *position*, so two priors carrying identical bytes both
    count — see ``derive_trace_lineage``'s note on why keying by ``payload_hash``
    here is wrong. A leg cannot arrive twice (its key is unique), so no dedup guard
    is needed.
    """
    if leg.payload_hash is None:
        return  # not lineage-bearing; must not count toward |inbound| (see D6/#120)
    consumer = entities.get(_consumer_id(leg))
    if consumer is None:
        return
    node = memory.memory_node_for_leg(consumer, leg)
    key = (leg.interaction_id, leg.leg_type)
    if memory.accumulates(consumer):
        retained.setdefault(node, []).append(key)
    else:
        retained[node] = [key]
