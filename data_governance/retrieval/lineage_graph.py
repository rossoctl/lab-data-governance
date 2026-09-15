"""Retrieval submodule — the **Data lineage** traversal and summary reads (D14).

Part of the :mod:`data_governance.retrieval` package. Sibling to :mod:`.lineage`,
which serves the *per-leg* metadata triple as a flat list. This module serves the
two reads ADR-0028 **D14** puts at the other two grains:

- **entity grain** — :func:`get_lineage_graph`: ``fanin`` (upstream / ancestors) or
  ``fanout`` (downstream / descendants) of one **data source**, seeded at one
  **Entity**, over one trace.
- **trace grain** — :func:`get_lineage_summary`: ``list sources`` and
  ``list destinations`` for one trace.

A separate module rather than more of :mod:`.lineage` because that module's contract
is a **pure lookup** (ADR-0028 D7) — one ``SELECT``, no derivation — and a traversal
is a different kind of read. Keeping them apart stops the lookup's guarantee from
being quietly weakened by a walk sharing its file.

**THE EDGE RULE.** This is the whole of the design, and every clause is load-bearing::

    Arriving at entity A at sequence position `s`, a hop A -> B is followed iff:
      1. the trace has an Interaction leg whose per-leg direction runs A -> B;
      2. that leg has a derived lineage_metadata row;
      3. the seeded `source` is a MEMBER of that row's stored `data_sources`; and
      4. the leg's `seq` is strictly later than `s` (fanout) / earlier (fanin).

- **The trace supplies the candidate edges; the metadata supplies whether lineage
  actually flowed along them** (D14's wording, literally). Neither table can answer
  alone: ``lineage_metadata`` records *sets* and deliberately no edges (D10), while
  ``interaction_legs`` records edges and knows nothing about provenance.
- **A leg with no derived row is not an edge.** That is the spec's rule — "if there
  is no lineage through an entity that Entity is the end of fanin or fanout"
  (``data_lineage_alg.md`` "API") — so the walk stops where provenance stops rather
  than where the call graph happens to end.
- **Clause 3 is the source scoping**, and it is the spec's own sentence: "we should
  traverse an edge towards the next/previous entity based iff the source is part of
  the edge/interaction metadata sources". The ``source`` is the thing being *traced*,
  so it is held **constant** for the whole walk — it is not a per-hop comparison
  against the previously-visited entity. A leg whose ``data_sources`` does not contain
  it is a leg this source's content demonstrably did not travel on, so the walk must
  not cross it even though some *other* source's content did.
- **Clause 4 is the sequence scoping.** Data cannot flow backwards in time, so an
  entity's downstream is what happened *after* the content arrived. ``seq`` is the
  per-leg execution cursor (ADR-0025 puts it on the leg; the parent ``interactions``
  row has none), and it both **gates** which edges are eligible and **orders** them —
  the spec: "the interaction sequence number governs the edges to be considered and
  their order (fanout - larger numbers, fanin - smaller numbers)".
- **Per-leg direction, never the interaction's caller->callee.** A response leg
  travels callee -> caller. This mirrors ``traversal._producer_id`` /
  ``_consumer_id`` and the UI's ``legDirection`` (``ui/src/lib/flow.ts``). Keying on
  the interaction's fixed direction would miss every response — and an agent's data
  mostly *arrives* as the responses to calls it made (ADR-0025), so that reading
  would drop the majority of real inbound flow.

**Why clauses 3 and 4 are not optional refinements.** The first shipped
implementation had only clauses 1-2: it reduced the lineage row to a boolean
``has_lineage`` and let ``seq`` ride along unused, sorting the reported legs and
gating nothing. That version is not a coarser answer, it is a *different and false*
one. On the live trace ``e62610bec7e8c1f4372aacc392eb9be5``, seeding the leaf tool
``search_destinations`` — which touches exactly two legs, seq 2 inbound and seq 3
outbound — returned **all 10 other entities and all 50 legs for BOTH directions,
byte-identical** (same membership *and* same ``hops`` per entity), including
``charge_card``, whose only legs are at seq 39/40 and whose ``data_sources`` never
mention ``search_destinations``. Fan-in equalled fan-out because :func:`_adjacency`
builds fan-in as the exact edge-reversal of fan-out, and once time is discarded the
aggregate request+response edge set is symmetric, so reversing it is a no-op — the
reversal was correct, it simply had no purchase. Under this module's rule the same seed
answers ``fanout`` with 7 entities / 36 legs and ``fanin`` with nothing (the source
*originates* at that tool, so it has no ancestors).

Those two figures are a **measurement against that one live trace**, not a regression
guard: they are reproducible by hand but deliberately not asserted anywhere, because a
test pinning them would be pinning the demo data rather than this module's rule. What the
suites do pin is the *shape* of the same claim on small synthetic fixtures — fan-in and
fan-out differing on a symmetric edge set, and an entity whose legs are all seq-earlier
being absent from a fanout. Do not read the numbers here as covered by a test.

**No matcher runs here** (ADR-0028 D7). These reads re-walk structure and read
*persisted* verdicts; matching happened at ingest. An implementation that finds
itself wanting a matcher call to answer a hop has violated D7 — the edge should have
been persisted instead. Clause 3 is where that is easiest to get wrong: it is a **set
membership test against the stored array**, never a re-derivation. Lineage is *read*
here, not recomputed — no matching, no inference, no re-attribution of a source to a
payload. If a walk ever needs to *decide* whether a source belongs to a leg, the
answer is to persist it at ingest, not to decide it here.

**Intra-trace only** (D14). Every query is scoped through ``interactions.trace_id``,
so "ancestors" means ancestors *within this trace*. Flow through shared persistent
storage across traces is Step II and deferred; a walk that crossed a trace boundary
would be a false cross-trace data-flow claim, the one thing a governance tool must
not make.

**What an empty answer means, and why the state is a third field.** An empty entity
list has several unrelated causes — nothing adjacent to the seed, adjacency that exists
but is not derived yet, adjacency that *is* derived but does not carry this source, or
a seed that is not in the trace at all. Collapsing them would let "not computed yet"
read as "no sources", which is the same failure D6's three-valued status exists to
prevent one level up. So :class:`GetLineageGraphResult` carries ``state`` and
``pending_frontier`` beside the lists, and the caller is told *why* the walk stopped
rather than being handed a bare empty set.

**The two ways a hop can fail are DIFFERENT FACTS and never collapse.** This is the
crux of the tri-state discipline once clause 3 exists:

- a leg with **no derived row yet** is "ask again later" — it goes on
  ``pending_frontier``, and the answer may grow;
- a leg with **a derived row that does not contain this source** is a real, *final*
  answer: lineage for this source does not flow here. It is silently not an edge, and
  it must NOT appear on the frontier, because no amount of waiting will change it.

The ``LEFT JOIN`` plus null-probe in :func:`_fetch_legs` exists precisely to keep
those apart. Merging them either way is a lie: treating the undelivered case as final
under-reports a still-arriving answer, and treating the source-absent case as pending
sends a caller back to poll forever.

**Accepted degeneracy, already recorded in D14.** Under the trivial ``simple_match``
every leg gets a derived row *and* inherits every upstream source, so clause 3 prunes
little and ``fanout`` approaches the whole seq-forward reachable call graph. These
reads inherit matcher quality exactly as the triple does — though note clause 4 prunes
regardless of matcher quality, because it is a fact about the trace's own ordering
rather than about provenance. That is why ``pending_frontier`` and ``truncated`` are
reported: a full fanout must be readable as "nothing pruned it", not as "provenance
was exhaustively traced".
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
    "MissingSource",
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


class MissingSource(ValueError):
    """*source* was absent or empty.

    ``source`` is **required**, for the same reason ``direction`` is: it is half the
    question. "What did this entity's data reach" is not answerable without saying
    *whose* data — an entity handles content from several sources at once (on the live
    corpus a mid-trace agent leg routinely carries four or five), and each has its own
    fanout. There is no defensible default: the union over all sources is the
    *multi-source* read the spec explicitly defers ("Given multiple sources - semantics
    are not clear: Do we expect the exact set of sources? Any of them?",
    ``data_lineage_alg.md`` "deferred issues"), so silently answering it would ship a
    guess at an open design question under the name of a settled one.

    **Rejected: optional, defaulting to the untimed source-less walk.** It would have
    kept the old callers working. But that walk is not a weaker version of this one, it
    is a false one (see the module docstring's live reproduction), so keeping it
    reachable behind a default would leave the wrong answer as the easiest to ask for.

    Distinct from an *unknown* source, which is NOT an error — see
    :func:`get_lineage_graph`.
    """


# ---------------------------------------------------------------------------
# Return types — field names serialize verbatim to the wire shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageGraphEntityView:
    """One **Entity** the walk reached, with how far away it is.

    ``hops`` is the *fewest* hops from the seed **along a path that respects the edge
    rule** — every leg on it carries the source, and its seqs run monotonically away
    from the seed. That qualifier is the whole subtlety: a plain hop-BFS no longer
    yields it, because the shortest *structural* route may be closed to this source or
    run backwards in time while a longer route is open. :func:`_walk` therefore
    computes it explicitly rather than reading it off arrival order; see its docstring.

    It is a distance, not an ordering of the flow — two entities at the same depth were
    reached by different routes and the lineage algebra has no truthful interleaving to
    offer (D10). ``seq`` on the reported legs is the only ordering the schema genuinely
    supports.

    The seed entity itself is **not** in this list (it is the question, not the
    answer). It can still legitimately appear if the trace's flow returns to it —
    a genuine cycle — in which case it carries the hop count of that return.
    """

    id: str
    natural_key: str | None
    kind: str | None
    display_name: str | None
    hops: int
    # A pod's Kubernetes namespace (wire contract v1.7, migration 0020); None for
    # a non-pod entity. Two same-named pods now differ here, not only in the key.
    namespace: str | None = None


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
    """One source's lineage reachability from one entity over one trace, plus why the
    walk stopped.

    ``source`` is echoed back because the answer is meaningless without it: the same
    seed entity has a different fanout per source it handled, so a cached or logged
    result that lost the source would be unattributable.

    ``state`` is three-valued and must not be inferred from ``entities`` being
    empty:

    - ``"derived"`` — the walk followed at least one eligible hop. ``entities`` is
      the answer.
    - ``"pending"`` — the seed has adjacent legs in the trace but none of them has
      a derived lineage row yet. **Not** "no sources": the eventual-consistency
      window, and the entities involved are named in ``pending_frontier``.
    - ``"no-adjacent"`` — no leg is available to leave the seed on. This covers the
      trace having no leg touching the seed at all, and the case that clause 3 and 4
      added: every candidate leg is derived but none carries this source, or none is on
      the right side of the seed's arrival ``seq``. Those are all *complete* answers —
      lineage for this source genuinely does not flow onward from here — which is why
      they share a state with "nothing there" rather than getting a fourth value.

      **Rejected: a fourth ``"source-absent"`` state.** It would name the distinction,
      but not usefully: every one of these is the same actionable fact ("this is the
      end of the fanout"), and a caller cannot do anything different with them. The
      distinction that *does* change caller behaviour — final versus not-yet — is
      already carried, by ``pending`` and ``pending_frontier``.

    ``pending_frontier`` lists entities the walk reached but could not continue
    through, because the onward leg carries no derived lineage row **yet**. It is the
    honest distinction between "provenance genuinely ends here" and "P-data-lineage
    has not got here yet" — two facts an empty tail cannot tell apart. A consumer
    reading a frontier should expect the answer to grow.

    A leg that IS derived but whose ``data_sources`` lacks ``source`` is deliberately
    **not** on the frontier: that is a settled answer, not a pending one, and polling
    for it would never terminate. Same for a leg on the wrong side of the seq boundary.

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
    source: str = ""
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
    """One directed, lineage-bearing hop, as the walk consumes it.

    ``seq`` is no longer decoration. In the first shipped version it rode along here
    and was used only to sort the reported legs; now it *gates* the hop (edge-rule
    clause 4), which is the difference between a lineage answer and a call-graph one.
    """

    to_entity_id: str
    interaction_id: str
    leg_type: str
    seq: int


# The seed's virtual arrival position. The seed has not arrived *on* a leg — it is
# where the question starts — so it must be able to depart on any eligible leg
# whatsoever. Using +/-infinity rather than the seed's earliest/latest touching leg is
# deliberate: picking a real leg would silently narrow the question to "downstream of
# that particular arrival", and the caller asked about the entity, not about one leg of
# it. `float` and not a large int so no real `seq` can ever equal it.
#
# The sign also makes the seed unbeatable under `_walk`'s `more_permissive`, which is
# what it should be: no arrival can open more onward legs than "no constraint at all",
# so a cycle returning to the seed can never displace its virtual arrival.
_SEED_ARRIVAL = {FANOUT: float("-inf"), FANIN: float("inf")}


def _walk(
    derived: dict[str, list[_Edge]],
    undelivered: dict[str, list[tuple[str, int]]],
    seed: str,
    direction: str,
    *,
    max_hops: int = _MAX_HOPS,
    max_entities: int = _MAX_ENTITIES,
) -> tuple[dict[str, int], list[LineageGraphLegView], list[str], bool]:
    """Sequence-aware breadth-first walk from *seed*, returning
    ``(hops_by_entity, legs, pending_frontier, truncated)``.

    *derived* is the adjacency of hops that have a lineage row **carrying the seeded
    source** — clause 3 was applied when this map was built (:func:`_adjacency`), so
    every edge in here is already source-eligible and the walk only has to enforce the
    seq rule. *undelivered* is ``entity -> [(far_entity, seq), ...]`` for legs that touch
    an entity but carry no derived row **yet**; it is never followed, only *reported*,
    which is what turns a silent dead end into ``pending_frontier``. It carries ``seq``
    because clause 4 applies to it as well — see the loop below. A derived leg that
    simply lacks the source appears in *neither* map: that is a final "no", not a
    pending one.

    **The seq rule (clause 4), and why it is strict.** Arriving at an entity at
    position ``s``, ``fanout`` may only depart on legs with ``seq > s`` and ``fanin``
    only on ``seq < s``. Strict, not ``>=``, and the corpus settles it rather than
    taste: a request and its response are two *different* legs at two different seqs
    (ADR-0025 splits them, and on trace ``e62610bec7e8c1f4372aacc392eb9be5`` the
    ``search_destinations`` call is seq 2 request / seq 3 response), so a genuine
    round trip never needs ``>=`` to be expressible. Two legs sharing a ``seq`` is
    impossible — ``seq`` is a per-leg cursor drawn from a sequence — so ``>=`` could
    only ever re-admit the leg the walk just arrived on, i.e. let data flow straight
    back where it came from in zero time. That is a false hop, so strict it is.

    **``visited: set[str]`` would now be UNSOUND, and this is the subtle part.** With
    clause 4 an entity can be legitimately re-entered at a *different* position, and a
    different arrival opens edges the first one could not take: an agent reached at seq
    30 may only leave on seq > 30, while the same agent reached at seq 10 may also leave
    on seq 20. A plain "seen it, skip it" set keeps whichever arrival happened to be
    dequeued first and silently drops every entity reachable only past the better one.
    This is the ordinary shape of the corpus, not a corner case: a coordinating agent is
    re-entered on every tool response it receives.

    The replacement is ``best_arrival[entity]``, the most **permissive** arrival seq seen
    — smaller for fanout, larger for fanin, since fanout departs on ``seq > arrival``.
    An entity is re-enqueued iff the new arrival **either** is strictly more permissive
    than the recorded one **or** reaches it at a strictly smaller depth. Two clauses,
    because permissiveness and depth are independent dominance axes:

    - an arrival opens exactly the legs on its permissive side, so a strictly more
      permissive one opens a *superset* and must be followed or everything beyond it is
      lost;
    - a *shallower* arrival opens no new legs, but reaches everything beyond it at a
      smaller depth — and ``hops`` is a distance, so dropping it reports a longer route
      than one that exists.

    A visit that is neither is genuinely redundant and is dropped.

    **Depth is deliberately not part of the PERMISSIVENESS test**, which is the trap one
    refinement in from the ``visited`` bug: a *deeper* arrival can be *more permissive* —
    reached the long way round but earlier in the trace — and it opens edges the shallow
    arrival cannot. Requiring "shallower AND more permissive" would reject it and lose
    everything beyond it. Hence two independent records, each keeping its own best:
    writing one from the other's winner reintroduces exactly the loss the other prevents.

    **How ``hops`` stays correct.** ``hops`` is the fewest hops along a path respecting
    both clauses, and plain arrival-order BFS does not deliver that for free — a
    structurally shorter route may be closed to this source or run the wrong way in time.
    Note the FIFO/non-decreasing-depth argument that used to sit here **was not
    sufficient**, and believing it was is what let a real defect through: the queue is a
    FIFO, but entries were being *dropped* by a stale-entry skip rather than merely
    reordered, so a shallow route could be discarded before it ever expanded and the
    entities beyond it were then only found the long way round. What actually makes the
    distance right is:

    1. the two-clause re-enqueue above, so a shallower arrival is always expanded even
       when a more permissive one has been recorded; and
    2. ``hops[target]`` written with ``min``, so a deeper revisit records reachability
       without lengthening the reported distance.

    ``min`` alone is not enough either — it rescues the dominated entity itself but
    nothing beyond it. See the long note at the top of the pop loop for the concrete
    four-leg fixture where that produced ``hops[y] == 3`` for a valid 2-hop path.

    **Termination**, on a genuinely cyclic graph, and it does *not* rest on the visited
    bookkeeping. Both records improve **monotonically in one direction only**, which is
    what bounds the enqueues. ``best_arrival[entity]`` is only ever replaced by a strictly
    more permissive value, drawn from the trace's **finite** set of leg seqs, so it can
    improve at most ``|legs|`` times per entity. ``best_depth[entity]`` is only ever
    replaced by a strictly *smaller* non-negative integer, so it can improve at most
    ``max_hops`` times per entity. Every enqueue is the entity's first or strictly
    improves one of the two, so enqueues are bounded by
    ``|entities| × (|legs| + max_hops + 1)`` and the queue drains. Removing the
    stale-entry skip therefore cost a constant factor of redundant pops, never
    termination — that skip was an optimisation, never the guarantee.

    The deeper reason is clause 4 itself: ``agent -> tool -> agent`` — the ordinary shape
    of every tool call, and a two-node cycle — cannot loop forever because each traversal
    must strictly **advance** ``seq``, and legs are finite. **The seq gate subsumes the
    old cycle guard.** ``best_arrival`` is a pruning optimisation; the seq monotonicity is
    the termination argument. Worth stating because a future editor relaxing clause 4 to
    ``>=`` would remove the termination guarantee, not merely widen the answer.

    Bounds are reported, never silently applied: hitting either cap returns
    ``truncated=True`` so the caller can tell a bounded answer from a complete one.
    """
    # `fanout` departs on larger seqs, `fanin` on smaller. One sign flip is the whole
    # of the direction's effect on the seq rule, which keeps a single comparison rather
    # than two mirrored branches that could drift apart.
    forward = direction == FANOUT

    def eligible(edge_seq: int, arrival: float) -> bool:
        """Clause 4. Strict — see the docstring's request/response evidence."""
        return edge_seq > arrival if forward else edge_seq < arrival

    def more_permissive(new: float, old: float) -> bool:
        """Does arriving at *new* leave strictly more onward legs open than *old*?

        **Note the sign, which is the opposite of the direction of travel** and is the
        easiest thing here to get backwards. ``fanout`` departs on ``seq > arrival``, so
        an *earlier* arrival opens strictly more onward legs — arriving at an agent at
        seq 10 can leave on seq 20, while arriving at the same agent at seq 30 cannot.
        Mirror for ``fanin``, which departs on ``seq < arrival`` and so prefers a
        *later* arrival. Getting this backwards silently under-reports: the walk keeps
        the more restrictive arrival and drops every entity only reachable past the
        better one.
        """
        return new < old if forward else new > old

    hops: dict[str, int] = {}
    legs: list[LineageGraphLegView] = []
    frontier: set[str] = set()
    truncated = False

    # **Two separate maps, and conflating them is a correctness bug.**
    #
    # `best_arrival[entity]` — the most PERMISSIVE arrival seq seen, which is the only
    # thing that decides whether re-expanding is worthwhile: an arrival opens exactly
    # the legs on its permissive side, so a strictly more permissive one opens a
    # superset and anything else opens a subset. Depth is deliberately NOT part of this
    # test. A *deeper* arrival can be more permissive (reached the long way round but
    # earlier in the trace), and it then opens edges the shallow one cannot — so
    # requiring "shallower, or equal depth and more permissive" would reject it and
    # silently lose every entity beyond it. That is the same class of under-reporting as
    # the old `visited: set[str]`, one refinement in.
    #
    # `hops[entity]` — the shortest depth at which the entity was reached at all, which
    # is what gets reported. Kept apart because the shortest route and the most
    # permissive route need not be the same route, and the answer wants one of each.
    seed_arrival = _SEED_ARRIVAL[direction]
    best_arrival: dict[str, float] = {seed: seed_arrival}
    # The shallowest depth at which each entity has been ENQUEUED. A third record,
    # distinct from both of its neighbours: `hops` is the *answer* (and excludes the
    # seed), while `best_arrival` tracks permissiveness rather than distance. It exists
    # because those two dominance axes are independent — an arrival can be more
    # permissive AND deeper — so neither map alone can decide whether a queue entry is
    # genuinely redundant. See the expand guard, which consults both.
    best_depth: dict[str, int] = {seed: 0}
    # A separate record of legs already reported, so a re-enqueued entity does not
    # duplicate the route. Keyed on the leg's identity plus the direction it was
    # crossed in, because the same leg can legitimately be crossed from both ends over
    # a trace (its two entities each depart on it under different arrivals).
    reported: set[tuple[str, str, str, str]] = set()
    queue: deque[tuple[str, int, float]] = deque([(seed, 0, seed_arrival)])

    while queue:
        current, depth, arrival = queue.popleft()

        # NO STALE-ENTRY SKIP HERE, DELIBERATELY. The removed version read as obviously
        # right and was wrong, so it is worth spelling out.
        #
        # It skipped a popped entry whenever a strictly more permissive arrival at
        # `current` had since been recorded, reasoning that the weaker visit can only
        # reach a subset of what the dominating one will. That much is true — but
        # SUBSET-OF-ENTITIES IS NOT SUBSET-OF-DISTANCES. The more permissive arrival is
        # typically the *deeper* one (it reached here the long way round, which is
        # exactly why it arrived earlier in seq), so every entity the weaker-but-
        # shallower visit would have reached at `depth + 1` was instead rediscovered
        # from the dominator at its own greater depth. `hops` is a distance, so the
        # answer reported a longer route than one that demonstrably exists.
        #
        # Concretely, with `seed -(50)-> x`, `seed -(5)-> mid`, `mid -(10)-> x` and
        # `x -(60)-> y`: candidates are walked in seq order, so `x` is first reached at
        # depth 1 via seq 50, then `mid` offers it again at depth 2 via seq 10. Seq 10 is
        # more permissive, so the depth-1 entry was skipped when it popped and `y` came
        # back at **3** hops — when `seed -> x -> y` is a valid 2-hop path, 60 > 50
        # satisfying clause 4 at every step.
        #
        # Guarding the skip on depth as well does NOT fix it, which is the subtle part
        # and was tried first: a single best-depth scalar per entity is written by
        # whichever arrival got there first, so the permissive-but-deeper arrival lowers
        # the very record the guard consults, and the shallow entry is skipped anyway.
        # Deciding it correctly needs the depth OF THE DOMINATING ARRIVAL — per-arrival
        # state, strictly more bookkeeping than the skip could ever save.
        #
        # So it is gone. It was always an optimisation, never a correctness device:
        # termination rests on `best_arrival` improving monotonically (see this
        # function's docstring) and on the expand guard below, which is what actually
        # bounds re-enqueues. A redundant pop costs one re-scan of `current`'s
        # candidates, and both of its side effects are idempotent — legs dedupe through
        # `reported`, and `hops` writes go through `min`.

        # Any leg touching `current` that has no derived lineage row *and* is on the
        # right side of the arrival is a place the walk could continue once the
        # derivation catches up. Record the entity on the far side; do not follow it.
        #
        # Clause 4 IS applied here, clause 3 is not, and the asymmetry is the point: a
        # leg's `seq` lives on `interaction_legs` and is known whether or not the lineage
        # row has landed, whereas its `data_sources` is exactly what has not landed. So
        # seq-ineligibility is already a settled "never an edge" and belongs excluded,
        # while source membership is genuinely unknown and the entity is honestly pending.
        for pending_id, pending_seq in undelivered.get(current, ()):
            if eligible(pending_seq, arrival):
                frontier.add(pending_id)

        if depth >= max_hops:
            # Reached the depth bound. Only cry truncation if there was actually
            # somewhere eligible left to go — a walk that merely ends on the boundary
            # is a complete answer.
            if any(eligible(e.seq, arrival) for e in derived.get(current, ())):
                truncated = True
            continue

        # Order the departures by seq, in the direction of travel: the spec makes the
        # sequence number govern "the edges to be considered and *their order*". It does
        # not change which entities are reachable, but it makes the walk deterministic
        # and makes the earliest-in-time route the one that claims a given depth.
        candidates = sorted(
            (e for e in derived.get(current, ()) if eligible(e.seq, arrival)),
            key=lambda e: (e.seq if forward else -e.seq, e.interaction_id, e.leg_type),
        )
        for edge in candidates:
            target = edge.to_entity_id
            new_depth = depth + 1
            new_arrival = float(edge.seq)
            known_arrival = best_arrival.get(target)
            first_visit = known_arrival is None

            # THE ENTITY BOUND IS CHECKED BEFORE THE LEG IS REPORTED, and the order is
            # the fix for a real defect: reporting first meant a truncated answer cited
            # legs whose target never appeared in `entities`. With `max_entities=3` over a
            # five-leaf hub the read returned 3 entities and 5 legs, two of them pointing
            # at entities the caller was never given — so a client drawing the route got
            # edges to nodes that do not exist. For a UI whose whole rule is that an
            # unknown must never be rendered as a verdict, a dangling edge is precisely an
            # unexplained node, and `truncated` does not excuse it: it says the answer is
            # incomplete, not that parts of it refer to nothing.
            #
            # A leg to an ALREADY-KNOWN entity is still reported (see below) — that hop is
            # part of the route and both its endpoints are in `entities`. Only the leg
            # that would introduce an entity we are declining to return is withheld.
            if first_visit and len(hops) >= max_entities and target != seed:
                # The entity bound, and only for a *newly* discovered entity — revisiting
                # one already counted costs no extra slot.
                #
                # `truncated` is the headline, but the entity is also NOT recorded as
                # pending: `pending_frontier` means "not derived yet, ask again later",
                # and this one is derived — we simply declined to return it. Conflating
                # the two would send a caller back to poll for something that will
                # never arrive without a wider bound.
                truncated = True
                continue

            # Every traversed leg is reported, including one that arrives at an
            # already-known entity: the hop really happened and is part of the route,
            # even though the entity is not newly reached. Dropping it would leave a
            # cycle drawn as a dead end. Deduped on the leg's identity plus the direction
            # it was crossed in, so a re-expanded entity does not duplicate the route.
            leg_key = (edge.interaction_id, edge.leg_type, current, edge.to_entity_id)
            if leg_key not in reported:
                reported.add(leg_key)
                legs.append(
                    LineageGraphLegView(
                        interaction_id=edge.interaction_id,
                        leg_type=edge.leg_type,
                        from_entity_id=current,
                        to_entity_id=edge.to_entity_id,
                        seq=edge.seq,
                    )
                )

            # The reported distance: shortest depth at which the entity was reached at
            # all. `min` because a *more permissive* revisit is usually also a *deeper*
            # one, and it must not lengthen the answer — the shallower route was real.
            # The seed is the question, not the answer, so it is excluded; a genuine
            # cycle back to it still shows up in `legs`.
            if target != seed:
                hops[target] = (
                    new_depth if target not in hops else min(hops[target], new_depth)
                )

            # Expand if this arrival opens legs the best-known one does not, OR if it
            # reaches the target more SHALLOWLY than anything enqueued for it so far.
            #
            # Both clauses are load-bearing, because permissiveness and depth are
            # INDEPENDENT dominance axes and neither implies the other:
            #   - more permissive → opens a strict superset of onward legs, so dropping
            #     it silently loses every entity beyond it (this clause was always here);
            #   - shallower → opens no new legs, but reaches everything beyond it at a
            #     smaller depth, and `hops` is a distance. Dropping it reports a longer
            #     route than one that exists — the `hops[y] == 3` case dissected in the
            #     stale-entry note at the top of this loop.
            # A visit that is neither is genuinely redundant: same-or-narrower legs at
            # the same-or-greater depth, so it can change no field of the answer.
            #
            # The two records are kept INDEPENDENTLY on purpose. Writing one from the
            # other's winner is the trap the removed skip fell into: a deep-permissive
            # arrival would raise the recorded depth (or a shallow-narrow one narrow the
            # recorded permissiveness), reintroducing the very loss each clause exists to
            # prevent.
            known_depth = best_depth.get(target)
            opens_more = first_visit or more_permissive(new_arrival, known_arrival)
            arrives_sooner = known_depth is None or new_depth < known_depth
            if not opens_more and not arrives_sooner:
                continue
            if opens_more:
                best_arrival[target] = new_arrival
            if arrives_sooner:
                best_depth[target] = new_depth
            queue.append((target, new_depth, new_arrival))

    # An entity the walk actually reached is not "pending" — an eligible route to it
    # exists, whatever else about it is undelivered.
    frontier -= set(hops)
    frontier.discard(seed)
    legs.sort(key=lambda leg: (leg.seq, leg.interaction_id, leg.leg_type))
    return hops, legs, sorted(frontier), truncated


def _adjacency(
    rows: list[tuple], direction: str, source: str
) -> tuple[dict[str, list[_Edge]], dict[str, list[str]]]:
    """Build the eligible and undelivered adjacency maps for *direction* / *source*.

    One pass over the trace's legs. Each row is
    ``(interaction_id, leg_type, seq, caller_id, callee_id, data_sources)``, where
    ``data_sources`` is the stored ``TEXT[]`` or ``None`` for a leg with no derived
    lineage row yet (the ``LEFT JOIN``'s null probe).

    **Per-leg direction** (the module docstring's edge rule clause 1): a request runs
    caller -> callee, a response runs callee -> caller. ``fanout`` follows that
    direction as-is; ``fanin`` follows it reversed, which is the entire difference
    between the two reads' *topology* — the seq rule in :func:`_walk` is what makes
    them differ in *content*.

    **Clause 3 is applied here**, and it is a plain ``in`` against the persisted array:
    lineage is READ, never recomputed (ADR-0028 D7). No matching, no normalisation, no
    prefix or fuzzy comparison — the natural keys in ``data_sources`` were written by
    the ingest-time derivation and are compared verbatim. Anything cleverer here would
    be re-deriving lineage on the read path, which is exactly what D7 forbids.

    The three outcomes are kept distinct, because two of them are different *facts*
    (see :class:`GetLineageGraphResult`):

    - ``data_sources is None`` — no derived row **yet**. Goes to *undelivered*, is
      never followed, and surfaces as ``pending_frontier``: ask again later.
    - ``source in data_sources`` — an eligible edge. Goes to *derived*.
    - derived but ``source not in data_sources`` — a **final** no. Goes to neither map:
      this source's content did not travel on this leg, and no amount of waiting
      changes that. Putting it on the frontier would be a false promise.

    *undelivered* carries the leg's ``seq`` alongside the far entity, so :func:`_walk`
    can apply clause 4 to it too. That matters: a leg's ``seq`` lives on
    ``interaction_legs`` and is therefore known **whether or not** the lineage row has
    landed. An undelivered leg on the wrong side of the arrival could never become an
    edge however the derivation turns out, so naming its entity as pending would promise
    growth that cannot happen — the same false promise as the source-absent case, and it
    would be missed by only filtering the derived map.

    A leg with an unresolved participant (either id ``NULL`` — the caller-inference
    window) contributes no edge in any map: there is no node to walk to or from,
    and inventing one would be a claim about an entity we cannot name.
    """
    derived: dict[str, list[_Edge]] = {}
    undelivered: dict[str, list[tuple[str, int]]] = {}
    for interaction_id, leg_type, seq, caller_id, callee_id, data_sources in rows:
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
        if data_sources is None:
            undelivered.setdefault(origin, []).append((destination, int(seq)))
        elif source in data_sources:
            derived.setdefault(origin, []).append(
                _Edge(
                    to_entity_id=destination,
                    interaction_id=interaction_id,
                    leg_type=leg_type,
                    seq=int(seq),
                )
            )
        # else: derived, but this source is not in it — a settled "no lineage here".
    return derived, undelivered


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _fetch_legs(tx: db.Transaction, trace_id: str) -> list[tuple]:
    """Every leg of *trace_id* with its parent's participants and its **stored**
    ``data_sources`` set (``None`` where lineage is not derived yet).

    One flat query, then the walk happens in Python. A recursive CTE was the
    alternative and is the shape ``processors/interactions/state.py`` uses for span
    trees — but that walks a single self-referential FK, whereas an edge here is
    derived from two tables plus the per-leg direction rule, a source-membership test
    and a seq comparison against the *arrival*. Encoding that into a recursive join
    condition would bury the edge rule in SQL and put the pending-frontier logic out of
    reach; a trace is bounded, and :func:`.interactions.get_interactions` already scans
    one three times.

    **``m.data_sources`` is selected, not a ``has_lineage`` boolean.** The first
    shipped version reduced the whole lineage row to ``(m.seq IS NOT NULL)``, which
    made the spec's hop rule untestable at any layer above: with only a boolean the
    walk cannot ask "is *this* source in this leg's set", so it degenerated into
    treating "this leg has some lineage row" as "lineage flowed here". Selecting the
    real set is the root fix, not a refinement of it.

    ``LEFT JOIN`` with a null-probe rather than an inner join, because the *absence* of
    the lineage row is itself an answer here (it is what ``pending_frontier`` reports),
    and it is a **different** answer from a row that exists without this source. Those
    two must not collapse — see :func:`_adjacency`.

    The probe is now ``m.data_sources IS NULL`` from the same column the walk reads,
    rather than the separate ``m.seq`` probe :func:`.lineage._lineage_view` uses. Both
    are sound for the reason that module states — every metadata column is ``NOT
    NULL``, so a ``NULL`` can only mean "no row", never "a derived row whose triple is
    empty" — and reading the probe off the column being fetched keeps the two facts
    from drifting apart in a way one extra column could not justify.

    ``source_transformations`` and ``entities`` are deliberately **not** selected. The
    spec's hop rule names only *sources* ("iff the source is part of the edge/
    interaction metadata sources"), and ``entities`` in particular would be the wrong
    set to test: it is an unordered "passed through here" claim (D10) that would make
    the walk hop to anything the metadata ever mentioned, defeating the traversal's
    whole purpose. Selecting them would also cost the payload of a JSONB map per leg
    for no read.

    Scoped through ``interactions`` because ``interaction_legs`` has no ``trace_id``
    (ADR-0025 keeps identity on the parent). Getting that join wrong would pull
    another trace's flow into the walk.
    """
    return tx.fetch_all(
        "SELECT l.interaction_id::text, l.leg_type::text, l.seq, "
        "       i.caller_entity_id::text, i.callee_entity_id::text, "
        "       m.data_sources "
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
) -> dict[str, tuple[str | None, str | None, str | None, str | None]]:
    """``entity_id -> (natural_key, kind, display_name, namespace)`` for *entity_ids*.

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
        f"SELECT id::text, natural_key, kind::text, display_name, namespace "
        f"FROM entities WHERE id IN ({placeholders})",
        entity_ids,
    )
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


def get_lineage_graph(
    trace_id: str, entity_id: str, direction: str, source: str
) -> GetLineageGraphResult:
    """Walk one trace's lineage for *source*, from *entity_id*, upstream or downstream.

    *direction* is ``"fanin"`` (upstream / ancestors) or ``"fanout"`` (downstream /
    descendants) and is **required** — see :class:`UnknownDirection`.

    *source* is a **data source natural key** and is likewise **required** — see
    :class:`MissingSource`. It is a key as stored in ``lineage_metadata.data_sources``
    (ADR-0027/0028: lineage stores natural keys, not entity ids), so the values that
    can be passed are exactly what :func:`get_lineage_summary`'s ``sources`` lists.

    Follows the module's edge rule: a hop exists where the trace has a leg running
    that way, that leg has a derived ``lineage_metadata`` row, *this source is in that
    row's* ``data_sources``, and the leg's ``seq`` is on the correct side of the
    arrival. So the walk ends where **this source's** provenance ends
    (``data_lineage_alg.md`` "API"). Intra-trace only (D14).

    **A single source only.** The spec defers multi-source semantics explicitly — "Given
    multiple sources - semantics are not clear: Do we expect the exact set of sources?
    Any of them?" — so this takes one key and answers the one question that *is*
    settled. A caller wanting several must ask several times and decide for itself how
    to combine them, which keeps the undecided union out of the served contract.

    **An unknown *source* is a valid, EMPTY answer — not a 404.** A key that matches no
    ``data_sources`` value anywhere in the trace yields ``state="no-adjacent"`` with
    empty lists, exactly as a seed entity with no legs does. Three reasons, all this
    repo's existing discipline:

    - It is the truthful answer to the question asked. "This source's data reached
      nothing from here" is a real finding, and it is what the walk genuinely computed:
      no eligible edge exists. A 404 would claim the *question* was malformed, which is
      a different and false claim.
    - **A 404 would have to be derived from absence, and absence is not yet knowledge
      here.** Deciding "this source is unknown to this trace" means scanning the
      trace's derived rows — but a trace mid-derivation has few or none, so the same
      key would 404 now and 200 later. That is precisely the failure D6's three-valued
      ``status`` and this module's ``state``/``pending_frontier`` exist to prevent:
      "we don't know yet" must never be served as "there is nothing". The tri-state
      already carries this correctly — a not-yet-derived trace answers ``"pending"``
      with a frontier, which a 404 would have destroyed.
    - It matches the collection-read convention already used one field over: an unknown
      *trace* and an unknown *seed entity* are both 200-with-empty here, and ``state``
      is how a caller distinguishes the cases. A source is the third coordinate of the
      same question and gets the same treatment.

    Rejected, then: validating *source* against the trace's source union and 404-ing.
    It costs an extra query to turn a correct answer into a wrong one, and it makes the
    endpoint's response depend on derivation progress.

    Returns a :class:`GetLineageGraphResult`. Read its ``state`` before its
    ``entities``: an empty list means several different things, and only
    ``"no-adjacent"`` means "there is genuinely nothing there". Empty — never an
    error — when the trace, the entity or the lineage migration is absent.

    Raises :class:`UnknownDirection` / :class:`MissingSource` on a malformed question.
    Those are the two failure modes here, and both are caller errors rather than
    missing-data cases.
    """
    if direction not in _DIRECTIONS:
        raise UnknownDirection(
            f"direction must be one of {sorted(_DIRECTIONS)}; got {direction!r}"
        )
    if not source:
        raise MissingSource(
            "source is required: a data source natural key, as listed by "
            "the data-lineage-summary read's `sources`"
        )
    with db.transaction() as tx:
        if not _lineage_tables_exist(tx):
            # No status either: the table it lives in may be equally absent, and
            # `_trace_status` is not safe to call before the probe.
            return GetLineageGraphResult(
                direction=direction, seed_entity_id=entity_id, source=source
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
                source=source,
                status=status,
                stopped_at_seq=stopped_at_seq,
            )

        derived, undelivered = _adjacency(rows, direction, source)
        hops, legs, frontier, truncated = _walk(
            derived, undelivered, entity_id, direction
        )

        # The three-valued state. Order matters: an eligible hop is the answer; with
        # none, an undelivered leg touching the seed means "not yet", and only the
        # absence of both means there is genuinely nothing to follow. Note the last
        # case now also covers "every candidate leg is derived but does not carry this
        # source" — a complete answer, which is why it shares `no-adjacent` rather
        # than getting a fourth value (see `GetLineageGraphResult`).
        if hops:
            state = "derived"
        elif frontier or undelivered.get(entity_id):
            # `undelivered.get(entity_id)` needs no seq filter: the seed's arrival is
            # unconstrained, so every leg leaving it is eligible by construction. (The
            # walk still filters, because that same loop runs at every later entity too.)
            state = "pending"
        else:
            state = "no-adjacent"

        metadata = _fetch_entities(tx, sorted(hops))
        entities = [
            LineageGraphEntityView(
                id=eid,
                natural_key=metadata.get(eid, (None, None, None, None))[0],
                kind=metadata.get(eid, (None, None, None, None))[1],
                display_name=metadata.get(eid, (None, None, None, None))[2],
                hops=depth,
                namespace=metadata.get(eid, (None, None, None, None))[3],
            )
            # Sorted by distance, then id — a stable order for a set whose
            # membership, not sequence, is the answer.
            for eid, depth in sorted(hops.items(), key=lambda kv: (kv[1], kv[0]))
        ]
        return GetLineageGraphResult(
            direction=direction,
            seed_entity_id=entity_id,
            source=source,
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
            f"SELECT id::text, natural_key, kind::text, display_name, namespace "
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
                    namespace=r[4],
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
