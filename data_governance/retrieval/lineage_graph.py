"""Retrieval submodule — the **Data lineage** traversal and summary reads (D14).

Part of the :mod:`data_governance.retrieval` package. Sibling to :mod:`.lineage`,
which serves the *per-leg* metadata triple as a flat list. This module serves the
two reads ADR-0028 **D14** puts at the other two grains:

- **entity grain** — :func:`get_lineage_graph`: ``fanin`` (upstream / ancestors) or
  ``fanout`` (downstream / descendants) from one **Entity**, over one trace.
- **trace grain** — :func:`get_lineage_summary`: ``list sources`` and
  ``list destinations`` for one trace.

A separate module rather than more of :mod:`.lineage` because that module's contract
is a **pure lookup** (ADR-0028 D7) — one ``SELECT``, no derivation — and a traversal
is a different kind of read. Keeping them apart stops the lookup's guarantee from
being quietly weakened by a walk sharing its file.

**THE EDGE RULE.** This is the whole of the design, and every clause is load-bearing::

    A hop A -> B exists iff the trace has an Interaction leg whose per-leg
    direction runs A -> B, AND that leg has a derived lineage_metadata row.

- **The trace supplies the candidate edges; the metadata supplies whether lineage
  actually flowed along them** (D14's wording, literally). Neither table can answer
  alone: ``lineage_metadata`` records *sets* and deliberately no edges (D10), while
  ``interaction_legs`` records edges and knows nothing about provenance.
- **A leg with no derived row is not an edge.** That is the spec's rule — "if there
  is no lineage through an entity that Entity is the end of fanin or fanout"
  (``data_lineage_alg.md`` "API") — so the walk stops where provenance stops rather
  than where the call graph happens to end.
- **Per-leg direction, never the interaction's caller->callee.** A response leg
  travels callee -> caller. This mirrors ``traversal._producer_id`` /
  ``_consumer_id`` and the UI's ``legDirection`` (``ui/src/lib/flow.ts``). Keying on
  the interaction's fixed direction would miss every response — and an agent's data
  mostly *arrives* as the responses to calls it made (ADR-0025), so that reading
  would drop the majority of real inbound flow.

**No matcher runs here** (ADR-0028 D7). These reads re-walk structure and read
*persisted* verdicts; matching happened at ingest. An implementation that finds
itself wanting a matcher call to answer a hop has violated D7 — the edge should have
been persisted instead.

**Intra-trace only** (D14). Every query is scoped through ``interactions.trace_id``,
so "ancestors" means ancestors *within this trace*. Flow through shared persistent
storage across traces is Step II and deferred; a walk that crossed a trace boundary
would be a false cross-trace data-flow claim, the one thing a governance tool must
not make.

**What an empty answer means, and why the state is a third field.** An empty entity
list has three unrelated causes — nothing adjacent to the seed, adjacency that exists
but is not derived yet, or a seed that is not in the trace at all. Collapsing them
would let "not computed yet" read as "no sources", which is the same failure D6's
three-valued status exists to prevent one level up. So :class:`GetLineageGraphResult`
carries ``state`` and ``pending_frontier`` beside the lists, and the caller is told
*why* the walk stopped rather than being handed a bare empty set.

**Accepted degeneracy, already recorded in D14.** Under the trivial ``simple_match``
every leg gets a derived row, so no hop is ever pruned and ``fanout`` approaches the
whole reachable call graph. These reads inherit matcher quality exactly as the triple
does. That is why ``pending_frontier`` and ``truncated`` are reported: a full fanout
must be readable as "nothing pruned it", not as "provenance was exhaustively traced".
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from data_governance import db
from data_governance.processors.data_lineage.memory import TARGET_KINDS

from .lineage import _lineage_tables_exist, _trace_status

__all__ = [
    "FANIN",
    "FANOUT",
    "GetLineageGraphResult",
    "GetLineageSummaryResult",
    "LineageGraphEntityView",
    "LineageGraphLegView",
    "UnknownDirection",
    "get_lineage_graph",
    "get_lineage_summary",
]

# The two directions, as the wire spells them. Module constants rather than an enum:
# they cross the HTTP boundary as a query-parameter string, and the API layer
# validates against exactly these values (there is no third reading of "which way
# does the question point").
FANOUT = "fanout"
FANIN = "fanin"
_DIRECTIONS = (FANIN, FANOUT)

# Walk bounds. A trace is bounded, but a governance read must not become unbounded
# work on a pathological one, and silently returning a partial walk is the failure
# this whole module is careful about — so hitting either cap sets `truncated`.
_MAX_HOPS = 64
_MAX_ENTITIES = 512


class UnknownDirection(ValueError):
    """*direction* was not ``fanin`` or ``fanout``.

    Raised rather than defaulted. Guessing which way a provenance question points
    would answer a question the caller did not ask — and the two answers are not
    interchangeable, so a wrong default is a wrong claim rather than a mild one.
    """


# ---------------------------------------------------------------------------
# Return types — field names serialize verbatim to the wire shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageGraphEntityView:
    """One **Entity** the walk reached, with how far away it is.

    ``hops`` is the *fewest* hops from the seed: the walk is breadth-first, so the
    first arrival at an entity is the shortest route to it. It is a distance, not an
    ordering of the flow — two entities at the same depth were reached by different
    routes and the lineage algebra has no truthful interleaving to offer (D10).

    The seed entity itself is **not** in this list (it is the question, not the
    answer). It can still legitimately appear if the trace's flow returns to it —
    a genuine cycle — in which case it carries the hop count of that return.
    """

    id: str
    natural_key: str | None
    kind: str | None
    display_name: str | None
    hops: int


@dataclass(frozen=True)
class LineageGraphLegView:
    """One **Interaction leg** the walk traversed — the route, not just the nodes.

    Reported so a caller can *draw* the answer rather than being handed a set of
    entities with no visible path between them. ``from_entity_id`` /
    ``to_entity_id`` are the **per-leg** direction (a response runs callee ->
    caller), which is the direction the walk actually followed.

    ``seq`` is the leg's own cursor value, so a caller can order the route in
    execution order — the one ordering the schema genuinely offers (ADR-0025: the
    parent ``interactions`` row has no ``seq``).
    """

    interaction_id: str
    leg_type: str
    from_entity_id: str
    to_entity_id: str
    seq: int


@dataclass(frozen=True)
class GetLineageGraphResult:
    """One entity's lineage reachability over one trace, plus why the walk stopped.

    ``state`` is three-valued and must not be inferred from ``entities`` being
    empty:

    - ``"derived"`` — the walk followed at least one derived hop. ``entities`` is
      the answer.
    - ``"pending"`` — the seed has adjacent legs in the trace but none of them has
      a derived lineage row yet. **Not** "no sources": the eventual-consistency
      window, and the entities involved are named in ``pending_frontier``.
    - ``"no-adjacent"`` — the trace has no leg touching the seed in this direction
      at all. This is the only state where an empty answer is a *complete* answer.

    ``pending_frontier`` lists entities the walk reached but could not continue
    through, because the onward leg carries no derived lineage row yet. It is the
    honest distinction between "provenance genuinely ends here" and "P-data-lineage
    has not got here yet" — two facts an empty tail cannot tell apart. A consumer
    reading a frontier should expect the answer to grow.

    ``truncated`` says a walk bound was hit, so the answer is a prefix of the real
    reachable set. Like D6's ``partial`` it is a *warning*, not an error.

    ``status`` / ``stopped_at_seq`` are the trace's D6 lineage coverage, read
    through :mod:`.lineage` rather than re-derived here: a ``"partial"`` trace's
    reachability is computed over a *prefix* of its legs, so the walk can be short
    for a reason that has nothing to do with the seed. ``None`` is *unknown*, never
    ``complete`` (ADR-0028 D6 "Reading the status").
    """

    direction: str
    seed_entity_id: str
    entities: list[LineageGraphEntityView] = field(default_factory=list)
    legs: list[LineageGraphLegView] = field(default_factory=list)
    state: str = "no-adjacent"
    pending_frontier: list[str] = field(default_factory=list)
    truncated: bool = False
    status: str | None = None
    stopped_at_seq: int | None = None


@dataclass(frozen=True)
class GetLineageSummaryResult:
    """A trace's ``list sources`` and ``list destinations`` (ADR-0028 D14).

    ``sources`` is the union of every derived leg's ``data_sources`` over the trace —
    a read of the **metadata triple**, not of the entity taxonomy. So it names the
    origins lineage actually attributed content to, which is a different set from
    "entities declared sources": under D12's kind defaults a delegation-shaped tool
    is declared a source while contributing nothing, and the two sets diverge there.
    Each element is an **Entity** natural key (spec rule 1), and the list is sorted —
    serialization only, the set is unordered.

    ``destinations`` is the trace's entities whose ``kind`` is a declared taxonomy
    **target**, read from the one named place
    (``processors.data_lineage.memory.TARGET_KINDS``) rather than hardcoded here.
    This is that column's first consumer (D14).

    **The two lists overlap in v1 for unrelated reasons.** ``SOURCE_KINDS`` and
    ``TARGET_KINDS`` both default to ``{"tool"}``, so a tool tends to appear in both.
    They are independent taxonomy columns that happen to share a default and diverge
    once the declared table distinguishes a read tool from a write one — early
    agreement between them is an artifact, not corroboration.

    Note the two are also at different grains, deliberately: ``sources`` are natural
    keys attributed by the derivation, ``destinations`` are entity rows of the trace.
    They are not two views of one list and must not be zipped.

    ``status`` / ``stopped_at_seq`` carry the trace's D6 coverage, because a
    ``"partial"`` trace's source union is a union over a *prefix*.
    """

    sources: list[str] = field(default_factory=list)
    destinations: list[LineageGraphEntityView] = field(default_factory=list)
    status: str | None = None
    stopped_at_seq: int | None = None


# ---------------------------------------------------------------------------
# The walk — pure, so the edge rule is testable without a database
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Edge:
    """One directed, lineage-bearing hop, as the walk consumes it."""

    to_entity_id: str
    interaction_id: str
    leg_type: str
    seq: int


def _walk(
    derived: dict[str, list[_Edge]],
    undelivered: dict[str, list[str]],
    seed: str,
    *,
    max_hops: int = _MAX_HOPS,
    max_entities: int = _MAX_ENTITIES,
) -> tuple[dict[str, int], list[LineageGraphLegView], list[str], bool]:
    """Breadth-first walk from *seed*, returning
    ``(hops_by_entity, legs, pending_frontier, truncated)``.

    *derived* is the adjacency of hops that HAVE a lineage row — the traversable
    graph. *undelivered* is the adjacency of legs that touch an entity but carry no
    derived row yet; it is never followed, only *reported*, which is what turns a
    silent dead end into ``pending_frontier``.

    Breadth-first specifically so a first arrival is a shortest route, which is the
    only reading of ``hops`` that survives a graph with more than one path to the
    same entity.

    **The visited set is load-bearing, not defensive.** This graph is genuinely
    cyclic: ``agent -> tool -> agent`` is the ordinary shape of every tool call, and
    an agent that calls itself or a tool that calls back is normal. That is the
    difference from the span-tree walks in ``processors/interactions/state.py``,
    which have no cycle guard because a span tree cannot have one. Without this set
    the ordinary case would not terminate.

    Bounds are reported, never silently applied: hitting either cap returns
    ``truncated=True`` so the caller can tell a bounded answer from a complete one.
    """
    hops: dict[str, int] = {}
    legs: list[LineageGraphLegView] = []
    frontier: set[str] = set()
    truncated = False

    visited: set[str] = {seed}
    queue: deque[tuple[str, int]] = deque([(seed, 0)])

    while queue:
        current, depth = queue.popleft()

        # Any leg touching `current` that has no derived lineage row is a place the
        # walk *could* have continued once the derivation catches up. Record the
        # entity on the far side and do not follow it.
        for pending_id in undelivered.get(current, ()):
            if pending_id not in visited:
                frontier.add(pending_id)

        if depth >= max_hops:
            # Reached the depth bound with somewhere still to go.
            if derived.get(current):
                truncated = True
            continue

        for edge in derived.get(current, ()):
            # Every traversed leg is reported, including one that arrives at an
            # already-visited entity: the hop really happened and is part of the
            # route, even though the entity is not newly reached. Dropping it would
            # leave a cycle drawn as a dead end.
            legs.append(
                LineageGraphLegView(
                    interaction_id=edge.interaction_id,
                    leg_type=edge.leg_type,
                    from_entity_id=current,
                    to_entity_id=edge.to_entity_id,
                    seq=edge.seq,
                )
            )
            target = edge.to_entity_id
            if target in visited:
                continue
            if len(hops) >= max_entities:
                # The entity bound. `truncated` is the headline, but the entity is
                # also NOT recorded as pending: `pending_frontier` means "not derived
                # yet, ask again later", and this one is derived — we simply declined
                # to return it. Conflating the two would send a caller back to poll
                # for something that will never arrive without a wider bound.
                truncated = True
                continue
            visited.add(target)
            hops[target] = depth + 1
            queue.append((target, depth + 1))

    # An entity the walk actually reached is not "pending" — a derived route to it
    # exists, whatever else about it is undelivered.
    frontier -= set(hops)
    frontier.discard(seed)
    legs.sort(key=lambda leg: (leg.seq, leg.interaction_id, leg.leg_type))
    return hops, legs, sorted(frontier), truncated


def _adjacency(
    rows: list[tuple], direction: str
) -> tuple[dict[str, list[_Edge]], dict[str, list[str]]]:
    """Build the derived and undelivered adjacency maps for *direction*.

    One pass over the trace's legs. Each row is
    ``(interaction_id, leg_type, seq, caller_id, callee_id, has_lineage)``.

    **Per-leg direction** (the module docstring's edge rule): a request runs
    caller -> callee, a response runs callee -> caller. ``fanout`` follows that
    direction as-is; ``fanin`` follows it reversed, which is the entire difference
    between the two reads.

    A leg with an unresolved participant (either id ``NULL`` — the caller-inference
    window) contributes no edge in either map: there is no node to walk to or from,
    and inventing one would be a claim about an entity we cannot name.
    """
    derived: dict[str, list[_Edge]] = {}
    undelivered: dict[str, list[str]] = {}
    for interaction_id, leg_type, seq, caller_id, callee_id, has_lineage in rows:
        if caller_id is None or callee_id is None:
            continue
        if leg_type == "response":
            producer, consumer = callee_id, caller_id
        else:
            producer, consumer = caller_id, callee_id
        # `fanin` asks "where did this come FROM", so it walks against the flow.
        origin, destination = (
            (producer, consumer) if direction == FANOUT else (consumer, producer)
        )
        if has_lineage:
            derived.setdefault(origin, []).append(
                _Edge(
                    to_entity_id=destination,
                    interaction_id=interaction_id,
                    leg_type=leg_type,
                    seq=int(seq),
                )
            )
        else:
            undelivered.setdefault(origin, []).append(destination)
    return derived, undelivered


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _fetch_legs(tx: db.Transaction, trace_id: str) -> list[tuple]:
    """Every leg of *trace_id* with its parent's participants and whether its
    lineage has been derived.

    One flat query, then the walk happens in Python. A recursive CTE was the
    alternative and is the shape ``processors/interactions/state.py`` uses for span
    trees — but that walks a single self-referential FK, whereas an edge here is
    derived from two tables plus the per-leg direction rule. Encoding that into a
    recursive join condition would bury the edge rule in SQL and put the
    pending-frontier logic out of reach; a trace is bounded, and
    :func:`.interactions.get_interactions` already scans one three times.

    ``LEFT JOIN`` with an ``m.seq IS NOT NULL`` probe rather than an inner join,
    because the *absence* of the lineage row is itself an answer here (it is what
    ``pending_frontier`` reports). ``m.seq`` is the probe column for the reason
    :func:`.lineage._lineage_view` gives: every metadata column is ``NOT NULL``, so
    it separates "no row" from "a derived row whose triple is empty".

    Scoped through ``interactions`` because ``interaction_legs`` has no ``trace_id``
    (ADR-0025 keeps identity on the parent). Getting that join wrong would pull
    another trace's flow into the walk.
    """
    return tx.fetch_all(
        "SELECT l.interaction_id::text, l.leg_type::text, l.seq, "
        "       i.caller_entity_id::text, i.callee_entity_id::text, "
        "       (m.seq IS NOT NULL) AS has_lineage "
        "FROM interaction_legs l "
        "JOIN interactions i ON i.id = l.interaction_id "
        "LEFT JOIN lineage_metadata m "
        "  ON m.interaction_id = l.interaction_id "
        " AND m.leg_type = l.leg_type "
        "WHERE i.trace_id = %s "
        "ORDER BY l.seq ASC",
        (trace_id,),
    )


def _fetch_entities(
    tx: db.Transaction, entity_ids: list[str]
) -> dict[str, tuple[str | None, str | None, str | None]]:
    """``entity_id -> (natural_key, kind, display_name)`` for *entity_ids*.

    Entities are cross-trace stable and carry no ``trace_id`` (ADR-0013), so this is
    keyed by id alone — the trace scoping already happened when the walk chose which
    ids to ask about. An id with no row simply does not appear, and the view then
    carries ``None`` metadata rather than being dropped: the walk *did* reach it, so
    silently omitting it would under-report the answer.
    """
    if not entity_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(entity_ids))
    rows = tx.fetch_all(
        f"SELECT id::text, natural_key, kind::text, display_name "
        f"FROM entities WHERE id IN ({placeholders})",
        entity_ids,
    )
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def get_lineage_graph(
    trace_id: str, entity_id: str, direction: str
) -> GetLineageGraphResult:
    """Walk one trace's lineage from *entity_id*, upstream or downstream.

    *direction* is ``"fanin"`` (upstream / ancestors) or ``"fanout"`` (downstream /
    descendants) and is **required** — see :class:`UnknownDirection`.

    Follows the module's edge rule: a hop exists where the trace has a leg running
    that way *and* that leg has a derived ``lineage_metadata`` row, so the walk ends
    where provenance ends (``data_lineage_alg.md`` "API"). Intra-trace only (D14).

    Returns a :class:`GetLineageGraphResult`. Read its ``state`` before its
    ``entities``: an empty list means one of three different things, and only
    ``"no-adjacent"`` means "there is genuinely nothing there". Empty — never an
    error — when the trace, the entity or the lineage migration is absent.

    Raises :class:`UnknownDirection` for any other *direction*. That is the one
    failure mode here, and it is a caller error rather than a missing-data case.
    """
    if direction not in _DIRECTIONS:
        raise UnknownDirection(
            f"direction must be one of {sorted(_DIRECTIONS)}; got {direction!r}"
        )
    with db.transaction() as tx:
        if not _lineage_tables_exist(tx):
            # No status either: the table it lives in may be equally absent, and
            # `_trace_status` is not safe to call before the probe.
            return GetLineageGraphResult(
                direction=direction, seed_entity_id=entity_id
            )

        status, stopped_at_seq = _trace_status(tx, trace_id)
        rows = _fetch_legs(tx, trace_id)
        if not rows:
            # An unknown trace, or one whose legs have not landed. The coverage
            # status still travels: it may already say `partial`, and that is a fact
            # about the trace rather than about this walk.
            return GetLineageGraphResult(
                direction=direction,
                seed_entity_id=entity_id,
                status=status,
                stopped_at_seq=stopped_at_seq,
            )

        derived, undelivered = _adjacency(rows, direction)
        hops, legs, frontier, truncated = _walk(derived, undelivered, entity_id)

        # The three-valued state. Order matters: a derived hop is the answer; with
        # none, an undelivered leg touching the seed means "not yet", and only the
        # absence of both means the trace genuinely has nothing adjacent.
        if hops:
            state = "derived"
        elif frontier or undelivered.get(entity_id):
            state = "pending"
        else:
            state = "no-adjacent"

        metadata = _fetch_entities(tx, sorted(hops))
        entities = [
            LineageGraphEntityView(
                id=eid,
                natural_key=metadata.get(eid, (None, None, None))[0],
                kind=metadata.get(eid, (None, None, None))[1],
                display_name=metadata.get(eid, (None, None, None))[2],
                hops=depth,
            )
            # Sorted by distance, then id — a stable order for a set whose
            # membership, not sequence, is the answer.
            for eid, depth in sorted(hops.items(), key=lambda kv: (kv[1], kv[0]))
        ]
        return GetLineageGraphResult(
            direction=direction,
            seed_entity_id=entity_id,
            entities=entities,
            legs=legs,
            state=state,
            pending_frontier=frontier,
            truncated=truncated,
            status=status,
            stopped_at_seq=stopped_at_seq,
        )


def get_lineage_summary(trace_id: str) -> GetLineageSummaryResult:
    """A trace's ``list sources`` and ``list destinations`` (ADR-0028 D14).

    ``sources`` unions the derived ``data_sources`` over the trace's legs — the
    metadata triple, not the taxonomy. ``destinations`` selects the trace's entities
    whose kind is a declared taxonomy target, from the one named place. See
    :class:`GetLineageSummaryResult` for why the two overlap in v1 and why they must
    not be read as two views of one list.

    Empty — never an error — before the lineage migration has run.
    """
    with db.transaction() as tx:
        if not _lineage_tables_exist(tx):
            return GetLineageSummaryResult()

        status, stopped_at_seq = _trace_status(tx, trace_id)
        # `unnest` in the target list flattens the TEXT[] sets; DISTINCT then unions
        # them, so the whole roll-up is one scan rather than a fetch-and-merge in
        # Python. Scoped through `interactions` (lineage_metadata has no trace_id).
        source_rows = tx.fetch_all(
            "SELECT DISTINCT unnest(m.data_sources) AS source "
            "FROM lineage_metadata m "
            "JOIN interactions i ON i.id = m.interaction_id "
            "WHERE i.trace_id = %s "
            "ORDER BY source ASC",
            (trace_id,),
        )
        # Entities of this trace whose kind is a declared target. Scoped via
        # `entity_spans` — the same subquery shape `interactions.get_entities` uses,
        # because an Entity has no trace_id of its own (ADR-0013).
        #
        # `entity_spans` is not probed by `_lineage_tables_exist` and does not need
        # to be: it lands in migration 0004 together with `entities` and
        # `interactions`, so the `interactions` probe already covers it (the same
        # reasoning `interactions._derived_tables_exist` states for probing one table
        # of that migration). Only `lineage_metadata` (0011) needed its own probe,
        # because it is a *different* revision.
        kinds = sorted(TARGET_KINDS)
        placeholders = ", ".join(["%s"] * len(kinds))
        destination_rows = tx.fetch_all(
            f"SELECT id::text, natural_key, kind::text, display_name "
            f"FROM entities "
            f"WHERE kind::text IN ({placeholders}) "
            f"  AND id IN (SELECT DISTINCT entity_id FROM entity_spans "
            f"             WHERE trace_id = %s) "
            f"ORDER BY natural_key ASC, id ASC",
            [*kinds, trace_id],
        )
        return GetLineageSummaryResult(
            sources=[r[0] for r in source_rows],
            destinations=[
                LineageGraphEntityView(
                    id=r[0],
                    natural_key=r[1],
                    kind=r[2],
                    display_name=r[3],
                    # A destination is not the result of a walk, so there is no
                    # distance to report. Zero rather than a nullable field: this
                    # list is "entities of the trace declared targets", a membership
                    # claim with no hop count to make.
                    hops=0,
                )
                for r in destination_rows
            ],
            status=status,
            stopped_at_seq=stopped_at_seq,
        )
