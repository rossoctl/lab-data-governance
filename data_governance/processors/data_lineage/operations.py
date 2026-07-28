"""The three data-lineage operations — the algebra, as pure functions (issue #117).

This is ``docs/data_lineage_alg.md`` "Lineage processing operations" transcribed
into code: ``init_lineage``, ``linear_lineage``, ``merge_lineage``. That document
is human-owned and authoritative; the construction rules below (transformation-set
union, key-collision merge, degrade-to-init) are its rules, cited per function.

**Pure.** No database, no trace, no I/O. Metadata in, metadata out, with the
matcher injected as a parameter. The trace traversal (:mod:`.traversal`) decides
*which* op applies to which leg and supplies the inputs; the driver
(:mod:`.driver`) reads and persists. Keeping the algebra separate from both is
what makes the spec's worked examples directly testable.

**Matching is a black box** (ADR-0027). These functions call the injected matcher
and read only ``matched`` and ``transformation`` off the result — never
``evidence``, and never anything about *which* matcher ran. The caller resolves it
through :func:`data_governance.matching.get_matcher`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
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
# alias — and lineage may only depend on the published contract (ADR-0027).
Payload = Any


@dataclasses.dataclass(frozen=True, slots=True)
class DataLineage:
    """The lineage metadata triple for ONE payload (spec "Lineage metadata"):

    1. ``data_sources`` — the set of origins the payload's content came from.
    2. ``source_transformations`` — ``data_source -> set<transformation>``. "order
       doesn't matter", hence a set per source rather than a list.
    3. ``entity_path`` — the ordered list of entities the data passed through.

    Frozen, with immutable members (``frozenset`` / ``tuple``), because a payload's
    lineage is a value that later ops *derive from* and must never edit in place:
    the spec says "create a copy" at every construction step, and one leg's
    persisted metadata is the input to many downstream legs.

    ``entity_path`` is a *deduplicated* order — "which entities did the data pass
    through", not a visit log. A merge of two branches sharing a prefix must not
    repeat the shared entities.
    """

    data_sources: frozenset[DataSource]
    source_transformations: Mapping[DataSource, frozenset[Transformation]]
    entity_path: tuple[str, ...]


def _extend_path(path: Iterable[str], entity_name: str) -> tuple[str, ...]:
    """Append *entity_name* to *path*, preserving first-arrival order and
    dropping duplicates (an entity the data already passed through does not
    reappear)."""
    out: list[str] = []
    for name in (*path, entity_name):
        if name not in out:
            out.append(name)
    return tuple(out)


def init_lineage(entity_name: str) -> DataLineage:
    """The starting point of a payload: it **originates here** (spec rule 1).

    "In this case the metadata is trivial:
      1. the data source is assigned the entity name
      2. A new map, setting a key - data source to an empty set of transformations
      3. A new list of entities which is empty"

    Note (3): the entity path is EMPTY, not ``(entity_name,)``. The originating
    entity is the *source*; it is not something the data passed *through*. It
    reappears in the path only once a downstream op extends the path with it.

    This is also the shape both other ops degrade to when the matcher reports no
    relationship (ADR-0027 D3(2)).
    """
    return DataLineage(
        data_sources=frozenset({entity_name}),
        source_transformations={entity_name: frozenset()},
        entity_path=(),
    )


def linear_lineage(
    payload: Payload,
    metadata: DataLineage,
    output_payload: Payload,
    entity_name: str,
    *,
    matcher: Matcher,
) -> DataLineage:
    """A **single** input payload processed to one output (spec rule 2).

    Calls ``match(payload, output_payload)`` — input first, output second, the
    spec's argument order (a matcher reporting a directional transformation such
    as summarization would report it backwards if these were swapped).

    - ``matched=False`` → "There is no lineage ... call init lineage": the output
      is a NEW origin rooted at the processing entity, and *all* the upstream
      metadata is discarded (ADR-0027 D3(2)). This is the anonymization /
      fresh-external-read case.
    - ``matched=True`` → "1. the data source is assigned the metadata data source;
      2. create a copy of the transformations and add the returned transformation
      (if exists) to **all** the transformation sets; 3. create a copy of the
      entity list and extend it with the entity name".

    "(if exists)": a ``None`` transformation adds nothing — it must not become a
    set member, since ``None`` is "no transform performed or none identified", not
    a kind of transformation.
    """
    result = matcher(payload, output_payload)
    if not result.matched:
        return init_lineage(entity_name)
    inherited = _inherit(metadata, result.transformation)
    return dataclasses.replace(
        inherited, entity_path=_extend_path(inherited.entity_path, entity_name)
    )


def _inherit(
    metadata: DataLineage,
    transformation: Transformation | None,
) -> DataLineage:
    """One matched input's contribution, WITHOUT the processing entity appended:
    copy its metadata and stamp *transformation* onto every source's set.

    The shared body of rule 2's matched branch (2(1)+2(2)) and rule 3's per-input
    step. The path extension (2(3) / 3(3)) is deliberately NOT here: it happens once
    per *operation*, after any merging, because the data passes through the
    processing entity once no matter how many inputs it consumed — appending
    per-contribution would put the entity mid-path in a merge whose later branch
    carries entities of its own.
    """
    added = frozenset({transformation}) if transformation is not None else frozenset()
    return DataLineage(
        data_sources=frozenset(metadata.data_sources),
        source_transformations={
            source: transformations | added
            for source, transformations in metadata.source_transformations.items()
        },
        entity_path=tuple(metadata.entity_path),
    )


def merge_lineage(
    inputs: Sequence[tuple[Payload, DataLineage]],
    output_payload: Payload,
    entity_name: str,
    *,
    matcher: Matcher,
) -> DataLineage:
    """**Multiple** input payloads to one output (spec rule 3, generic form).

    "the idea here is to call the matching function with every source payload and
    output payload (e.g. payload_a,output_payload; payload_b,output_payload, ..)".
    Only the inputs that MATCH contribute; each contributes with its own reported
    transformation attached (per-source, not pooled). Then:

    1. the data sources are the **union** of the matching inputs' sources;
    2. the maps are merged, "merge the transformation sets in case a key appears
       twice" — a source reached through two different inputs collects the
       transformations of both paths (spec Example 2(2));
    3. the entity lists are merged and extended with the entity.

    With no matching input at all the op degrades to :func:`init_lineage`
    (ADR-0027 D3(2)) — a runtime *result* of matching, not a selection branch: the
    traversal still chose ``merge``; matching simply found nothing to inherit.

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
    path: tuple[str, ...] = ()
    for contribution in contributions:
        sources |= contribution.data_sources
        for source, values in contribution.source_transformations.items():
            # Key collision: union the sets rather than let the last writer win.
            transformations[source] = transformations.get(source, frozenset()) | values
        for name in contribution.entity_path:
            path = _extend_path(path, name)
    # The processing entity is appended ONCE, at the end, so the merged path ends
    # where the data now is. `_extend_path` dedupes, so branches sharing a prefix
    # keep each entity once in first-arrival order, and an entity the data already
    # passed through does not reappear because it is also the processing entity.
    return DataLineage(
        data_sources=frozenset(sources),
        source_transformations=transformations,
        entity_path=_extend_path(path, entity_name),
    )
