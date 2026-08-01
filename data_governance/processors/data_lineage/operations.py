"""The two data-lineage operations — the algebra, as pure functions (issue #117).

This is ``docs/data_lineage_alg.md`` "Lineage processing operations" transcribed
into code: ``init_lineage`` and ``merge_lineage``. That document is human-owned and
authoritative; the construction rules below (transformation-set union,
key-collision merge, the entity's own empty transformation set, degrade-to-init)
are its rules, cited per function.

**Two operations, not three** (ADR-0028 D11). The spec once named a separate
single-payload ``linear_lineage``; it now defines one generic ``merge_lineage``
covering "a single or multiple payloads", and a merge over one input computes the
identical triple a linear did — union of one source set is that set, and there is
nothing for the key-collision merge to collide. The two were always the same
function at different arities.

**Pure.** No database, no trace, no I/O. Metadata in, metadata out, with the
matcher injected as a parameter. The trace traversal (:mod:`.traversal`) decides
*which* op applies to which leg and supplies the inputs; the driver
(:mod:`.driver`) reads and persists. Keeping the algebra separate from both is
what makes the spec's worked examples directly testable.

**Matching is a black box** (ADR-0028). These functions call the injected matcher
and read only ``matched`` and ``transformation`` off the result — never
``evidence``, and never anything about *which* matcher ran. The caller resolves it
through :func:`data_governance.matching.get_matcher`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from data_governance.matching import Matcher, Transformation

# A data source is an **Entity**'s natural key (spec rule 1: "the data source is
# assigned the entity name"), so lineage names participants the same way the
# `entities` table does.
DataSource = str

# What we hand the matcher. Opaque by contract: "which aspect of a payload matters
# is a matcher's business" (``matching.contract``), and the trivial default reads
# neither argument. Named here rather than imported because ``matching``'s public
# surface exports the verdict types and ``get_matcher`` — not its internal payload
# alias — and lineage may only depend on the published contract (ADR-0028).
Payload = Any


@dataclasses.dataclass(frozen=True, slots=True)
class DataLineage:
    """The lineage metadata triple for ONE payload (spec "Lineage metadata"):

    1. ``data_sources`` — the set of origins the payload's content came from.
    2. ``source_transformations`` — ``data_source -> set<transformation>``. "order
       doesn't matter", hence a set per source rather than a list.
    3. ``entities`` — the **set** of entities the data passed through.

    Frozen, with immutable members (``frozenset``), because a payload's lineage is
    a value that later ops *derive from* and must never edit in place: the spec
    says "create a copy" at every construction step, and one leg's persisted
    metadata is the input to many downstream legs.

    ``entities`` is an **unordered set** — the spec says so outright: "the set of
    entities - through which entities the data passed through / Note: this is
    unordered. In case an order is needed - it will need to be derived from the
    trace using an API." It answers "*which* entities did the data pass through",
    never "in what order". A ``frozenset`` rather than an ordered container so the
    type itself refuses to carry an order that the algebra does not guarantee —
    a merge of two branches has no single truthful interleaving to offer.
    """

    data_sources: frozenset[DataSource]
    source_transformations: Mapping[DataSource, frozenset[Transformation]]
    entities: frozenset[str]


def init_lineage(entity_name: str) -> DataLineage:
    """The starting point of a payload: it **originates here** (spec rule 1).

    "In this case the metadata is trivial:
      1. the data source is assigned the entity name
      2. A new map, setting a key - data source to an empty set of transformations
      3. A new set of entities which is empty"

    Note (3): the entity set is EMPTY, not ``{entity_name}``. The originating
    entity is the *source*; it is not something the data passed *through*. It
    joins the set only once a downstream op extends the set with it.

    This is also the shape :func:`merge_lineage` degrades to when the matcher
    reports no relationship for *every* input (ADR-0028 D3(2)) — and it degrades
    there **regardless of ``is_entity_source``**: the spec's Example 3 states the
    rule for "false or true" alike, and the bracketed alternative reading beside it
    (``data_lineage_alg.md:104``) is an HTML comment the spec did not adopt.
    """
    return DataLineage(
        data_sources=frozenset({entity_name}),
        source_transformations={entity_name: frozenset()},
        entities=frozenset(),
    )


def _inherit(
    metadata: DataLineage,
    transformation: Transformation | None,
) -> DataLineage:
    """One matched input's contribution, WITHOUT the processing entity appended:
    copy its metadata and stamp *transformation* onto every source's set.

    The per-input step of the spec's matched branch (2(2)1 + 2(2)2). The entity
    extension (2(2)3) is deliberately NOT here: it belongs to the *operation*, which
    adds the processing entity once no matter how many inputs it consumed. (With set
    semantics the result would be the same either way — union is idempotent — but the
    rule stays where the spec puts it.)

    Nor does the entity's *own source contribution* belong here: an
    ``is_entity_source`` entity is added with an **empty** transformation set, so
    routing it through this function would wrongly stamp an inherited input's
    transformation onto it (ADR-0028 D12).
    """
    added = frozenset({transformation}) if transformation is not None else frozenset()
    return DataLineage(
        data_sources=frozenset(metadata.data_sources),
        source_transformations={
            source: transformations | added
            for source, transformations in metadata.source_transformations.items()
        },
        entities=frozenset(metadata.entities),
    )


def merge_lineage(
    inputs: Sequence[tuple[Payload, DataLineage]],
    output_payload: Payload,
    entity_name: str,
    *,
    matcher: Matcher,
    is_entity_source: bool = False,
) -> DataLineage:
    """**One or more** input payloads processed to one output (spec rule 2).

    The whole of the algebra bar the origin case — the spec's generic form covering
    "a single or multiple payloads", so a one-input call is a legitimate use and not
    a degenerate one (ADR-0028 D11: there is no separate ``linear_lineage``).

    "the idea here is to call the matching function with every source payload and
    output payload (e.g. payload_a,output_payload; payload_b,output_payload, ..)" —
    input first, output second, the spec's argument order (a matcher reporting a
    *directional* transformation such as summarization would report it backwards if
    these were swapped). Only the inputs that MATCH contribute; each contributes with
    its own reported transformation attached (per-source, not pooled). Then:

    1. the data sources are the **union** of the matching inputs' sources, "if
       is_entity_source == true: A ∪ B ∪ ... ∪ entity";
    2. the maps are merged, "merge the transformation sets in case a key appears
       twice" — a source reached through two different inputs collects the
       transformations of both paths (spec Example 2(2)) — and "add entity, with
       empty transformation set" when the entity is a source;
    3. "the sets of entities are merged, and extended with the entity" — a plain
       union across the contributions, which is also why merging two branches that
       share entities needs no special case.

    "(if exists)" on a transformation: a ``None`` adds nothing — it must not become a
    set member, since ``None`` is "no transform performed or none was identified",
    not a kind of transformation.

    **``is_entity_source``** (ADR-0028 D12) is a property of the producing *entity*,
    not of the payloads, which is why the caller supplies it rather than the op
    deriving it — the traversal reads :func:`.memory.is_entity_source`. It is
    orthogonal to ``matched``: ``matched`` decides whether upstream lineage is
    inherited, this decides whether the entity ALSO contributed content of its own.
    A tool that consumes its request and returns freshly-read data states both.
    Its transformation set is **empty** on purpose: the entity's own contribution
    did not undergo the transformation the *inherited* sources did, so stamping the
    match's transformation onto it would be a false claim.

    With no matching input at all the op degrades to :func:`init_lineage`
    (ADR-0028 D3(2)) — a runtime *result* of matching, not a selection branch: the
    traversal still chose ``merge``; matching simply found nothing to inherit. That
    branch **ignores ``is_entity_source``** (spec Example 3, stated for "false or
    true" alike): the output payload came from somewhere and its producer is the only
    origin left to name.

    *inputs* is a sequence of ``(payload, metadata)`` pairs rather than the spec's
    varargs so the arity is genuinely open (``inbound(i)`` can be any size) and the
    payload/metadata pairing cannot be split apart by a caller.
    """
    # One matcher call per input (the spec's "call the matching function with every
    # source payload and output payload"), each answered independently: an unmatched
    # input contributes nothing at all, not an empty contribution.
    contributions: list[DataLineage] = []
    for payload, metadata in inputs:
        result = matcher(payload, output_payload)
        if result.matched:
            contributions.append(_inherit(metadata, result.transformation))
    if not contributions:
        return init_lineage(entity_name)

    sources: set[DataSource] = set()
    transformations: dict[DataSource, frozenset[Transformation]] = {}
    entities: set[str] = set()
    for contribution in contributions:
        sources |= contribution.data_sources
        for source, values in contribution.source_transformations.items():
            # Key collision: union the sets rather than let the last writer win.
            transformations[source] = transformations.get(source, frozenset()) | values
        entities |= contribution.entities
    if is_entity_source:
        # The entity's OWN contribution (spec 2(2)1-2, ADR-0028 D12): it joins the
        # sources alongside what was inherited, with an EMPTY transformation set.
        # `setdefault`, not an assignment: were the entity already a source reached
        # through an input (an entity that appears upstream of itself in the trace),
        # overwriting would erase transformations that genuinely applied on that
        # path. Union with the empty set is a no-op, so this adds nothing false.
        sources.add(entity_name)
        transformations.setdefault(entity_name, frozenset())
    # Rule 2(2)3 — "the sets of entities are merged, and extended with the entity".
    # Union, so the result does not depend on the order the contributions were
    # visited in: branches sharing entities contribute them once, and the
    # processing entity is a member whether or not the data had reached it before.
    # Note this happens regardless of `is_entity_source` — the data demonstrably
    # passed *through* the producing entity either way.
    return DataLineage(
        data_sources=frozenset(sources),
        source_transformations=transformations,
        entities=frozenset(entities | {entity_name}),
    )
