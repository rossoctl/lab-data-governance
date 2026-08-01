/**
 * Pure highlight derivation for the **Lineage** view: given a selected **Entity**,
 * which nodes and edges of the Execution Flow graph carry that entity's data
 * sources.
 *
 * Sibling to `./graph.ts`, and deliberately NOT a replacement for it. `deriveGraph`
 * remains the single source of the node/edge set — this module consumes its output
 * and decides only what is *highlighted*. A second copy of the graph derivation
 * would let the two tabs disagree about which arrows exist, which is precisely the
 * bug the "one edge per leg, from `flatLegRows`" discipline in `graph.ts` was
 * written to make impossible. Render-free for the same reason every `lib/` module
 * here is: jsdom cannot measure an SVG, so the real coverage of this feature is the
 * pure test beside it (`lineageGraph.test.ts`), not a render assertion about pixels.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THE DEFINITION. "All the sources of the selected entity's data" is:
 *
 *   The union of `data_sources` over every **derived** lineage row whose leg's
 *   drawn edge has the selected entity as its **target** — i.e. every leg that
 *   DELIVERED data TO the entity — resolved from natural key to entity id.
 *   DIRECT sources only, one hop, never transitive.
 *
 * Every clause is load-bearing, so each is justified:
 *
 * 1. **Per-leg, rolled up to the entity.** Lineage is keyed at
 *    `(interaction_id, leg_type)` (ADR-0027 D5), not per entity, so a roll-up is
 *    unavoidable and the only question is which legs count. The alternative
 *    considered and REJECTED was "every leg where the entity is a participant",
 *    which would sweep in the legs the entity *sent* — the lineage of data leaving
 *    it, i.e. its own downstream contribution — and present that as an answer to
 *    "where did this entity's data come from". Inbound legs only is the direction
 *    the question asks. (For a leg where the entity is BOTH ends — a self-call —
 *    see clause 4.)
 *
 *    Note the direction used is the per-LEG one (`flow.legDirection`, carried on
 *    `GraphEdgeSpec.target`), not the interaction's caller→callee. A response leg
 *    travels callee → caller, so the response to a call the entity MADE is an
 *    inbound delivery to it, and it is counted. That is the substance of ADR-0025:
 *    an agent's data mostly arrives as the responses to its own calls, and a
 *    definition keyed on the interaction's fixed direction would miss all of it.
 *
 * 2. **The union of `data_sources`, not of `entities`.** The triple's `entities`
 *    element is "entities the data passed THROUGH" — transit, explicitly unordered
 *    (ADR-0027, and see `DataLineageView`'s note refusing to draw it as a chain).
 *    `data_sources` is the ORIGINS element, which is the one the question names.
 *    Highlighting transit nodes as sources would inflate the answer with
 *    intermediaries the backend did not call origins.
 *
 * 3. **Direct only, never transitive.** A source's own sources are NOT walked.
 *    This is the repo's central rule applied to a graph: a transitive claim the
 *    backend never derived would be the UI inventing lineage. It is also not even
 *    well-defined from the data in hand — the source is named by natural key, and
 *    "that entity's own sources" would mean re-running this same roll-up on it,
 *    whose result is the union over a DIFFERENT leg set and carries a different
 *    (possibly `pending`) derivation state. Composing two answers of different
 *    certainties into one highlight with no visible seam is exactly the silent
 *    under/over-report this module exists to avoid. Not offered, not behind a
 *    toggle: an option that can only produce an unciteable claim is not a feature.
 *
 * 4. **An entity can be a source of itself, and it is kept.** A self-call, or a
 *    lineage row that names the receiving entity among its own origins (data it
 *    contributed earlier and is now getting back), makes the selected entity appear
 *    in its own source set. That is a real fact the backend derived, so it is
 *    highlighted rather than filtered out — filtering would silently deny an
 *    origin. The reader can still tell it apart, because the selected node carries
 *    its own distinct marker (see {@link LineageHighlight.selectedNodeId}).
 *
 * 5. **Edges are highlighted too — the legs that CARRIED the data.** Only the
 *    contributing legs (those whose derived rows the union was taken from), not
 *    every edge touching a highlighted node. Without them the reader gets a set of
 *    lit nodes and no visible route between them, which in a dense graph is not an
 *    answer to "where did it come from" so much as a quiz. A leg whose lineage is
 *    derived but whose `data_sources` is EMPTY is still a contributing leg and is
 *    still highlighted: "this delivery originated here" is a real answer about that
 *    leg (ADR-0027 D3), and dimming the arrow would hide the delivery entirely.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * TRI-STATE, AND WHY THE STATE IS A UNION RATHER THAN AN EMPTY SET. `null` lineage
 * on a leg, and an ABSENT leg key, are both "not yet derived" — never "no
 * sources" — so a roll-up over legs that are all in that state must NOT return an
 * empty highlight set and let the view render it as "no sources found". The state
 * discriminates instead (see {@link LineageDerivationState}), the same judgement
 * `types.LineageState` makes for one leg and for the same reason: an empty set can
 * only spell two of the three facts. `status: 'partial'` rides along untouched so
 * the view can say the answer is a prefix (ADR-0027 D6).
 */
import type { DataLineageByLeg, DataLineage, LineageStatus } from '../types';
import { legLineageKey, type Entity, type Interaction } from './flow';
import { deriveGraph, type GraphSpec } from './graph';
import { entityIdsByKey } from './lineageLabels';

/**
 * A lineage source the graph CANNOT draw: its natural key matches no entity in
 * this trace, so there is no node to light up.
 *
 * EDGE CASE (unresolvable source). It happens legitimately: lineage is derived
 * from payload content and can cite an origin outside the trace's own entity set
 * (an upstream service the trace never spanned), or the entities read may simply be
 * behind. Either way the source is REAL and the graph is silent about it, which is
 * exactly the silent under-report `lib/graph`'s `dropped` exists to prevent — so it
 * is returned here for the view to disclose by count and by key, presented through
 * `lineageLabels.lineageLabel` like every other lineage key on screen.
 */
export interface UnresolvedLineageSource {
  /** The natural key the lineage row asserted. */
  naturalKey: string;
  /** How many contributing legs named it, so the notice can weigh it. */
  legCount: number;
}

/**
 * What the roll-up was able to establish, as three mutually exclusive facts.
 *
 * - `'derived'` — at least one contributing leg had a derived lineage row. The
 *   highlight is an answer. It is an answer *including* when
 *   `highlightedNodeIds` is empty: every contributing leg said "originates here"
 *   (ADR-0027 D3), which is the real verdict "this entity's data has no upstream
 *   sources in this trace".
 * - `'pending'` — legs deliver to this entity, but none of them has a derived
 *   lineage row yet (`null` values, or keys absent from the map entirely). *Wait.*
 *   Emphatically not `'derived'` with an empty set: that would render "we have not
 *   checked" as "we checked and found nothing".
 * - `'no-inbound'` — nothing delivers to this entity at all: no drawn edge targets
 *   it. Structurally distinct from `'pending'` — there is nothing to wait FOR, and
 *   no future derivation will change the answer — and from `'derived'`, because no
 *   lineage row was consulted. An isolated entity, or one that only ever sends,
 *   lands here.
 *
 * `'error'` is deliberately NOT an arm. Unlike `types.LineageState`, which is
 * handed one leg's outcome, this function is handed the already-reduced `byLeg`
 * map and cannot tell a failed read from an empty one. The view owns the query and
 * therefore owns that arm — see `FlowTables`/`LineageCoverageAlert`, which already
 * render `lineageQ.isError` as "coverage unknown". Inferring it from an empty map
 * here would be guessing.
 */
export type LineageDerivationState = 'derived' | 'pending' | 'no-inbound';

/** The whole highlight decision: what to light, what to say, how sure we are. */
export interface LineageHighlight {
  /**
   * The selected entity's own node id, or null when nothing is selected / the
   * selection names no node in this trace.
   *
   * Carried separately from `highlightedNodeIds` so the view can mark "this is the
   * one you asked about" distinctly from "this is where its data came from" — and
   * so a self-source (clause 4) reads as both rather than being indistinguishable
   * from an ordinary source.
   */
  selectedNodeId: string | null;
  /**
   * Node ids to highlight: the resolved DIRECT sources. Sorted for determinism (a
   * Set's iteration order is insertion order, which here depends on leg order, and
   * a stable snapshot is easier to assert and to diff).
   */
  highlightedNodeIds: string[];
  /**
   * Edge ids (`<interaction id>:<leg_type>`) to highlight: the contributing legs
   * — those whose derived lineage the union was taken from. In `seq` order,
   * inherited from `GraphSpec.edges`.
   */
  highlightedEdgeIds: string[];
  /** Sources that matched no entity in this trace. Never silently dropped. */
  unresolved: UnresolvedLineageSource[];
  /**
   * Natural keys claimed by more than one entity, so the view can disclose that a
   * highlighted node is an arbitrary (if deterministic) pick among them. See
   * `lineageLabels.entityIdsByKey`. Expected to be empty against a sane server.
   */
  ambiguousKeys: string[];
  /** How sure the answer is. See {@link LineageDerivationState}. */
  state: LineageDerivationState;
  /**
   * How many legs deliver to the selected entity, and how many of those had a
   * derived lineage row.
   *
   * Both counts, not a boolean: `derivedLegs < inboundLegs` is a *partial* roll-up
   * — some deliveries are answered and others are still in the
   * eventual-consistency window — which the view must be able to say out loud. A
   * single flag would collapse "3 of 3 answered" and "1 of 3 answered" into the
   * same `'derived'` and let a third of the picture go unmentioned.
   */
  inboundLegs: number;
  derivedLegs: number;
  /**
   * The trace's lineage coverage, passed straight through (ADR-0027 D6). Not
   * interpreted here: `'partial'` means the rows the union was taken from are a
   * PREFIX of the trace, so the answer is incomplete — a fact about the whole
   * trace that the view already has a component for (`LineageCoverageAlert`), and
   * that must not be folded into `state` where it would be mistaken for a property
   * of this one entity.
   */
  status: LineageStatus;
}

/** The empty answer, for "nothing is selected" and for an empty trace. */
const NO_SELECTION: LineageHighlight = {
  selectedNodeId: null,
  highlightedNodeIds: [],
  highlightedEdgeIds: [],
  unresolved: [],
  ambiguousKeys: [],
  // `'no-inbound'`, not `'pending'`: with no entity selected nothing has been
  // asked, so there is nothing to wait for. The view does not read `state` in this
  // case anyway — it renders its "select an entity" instruction off
  // `selectedNodeId === null` — but the value still has to be the non-claiming one.
  state: 'no-inbound',
  inboundLegs: 0,
  derivedLegs: 0,
  status: null,
};

export interface DeriveLineageHighlightArgs {
  entities: readonly Entity[];
  interactions: readonly Interaction[];
  /**
   * `(interaction_id, leg_type)` → that leg's nullable lineage, from
   * `useDataLineage`. `undefined` while the read is in flight (or absent), which
   * yields `'pending'` for any entity that has inbound legs — the honest answer,
   * since nothing is known yet.
   */
  byLeg: DataLineageByLeg | undefined;
  /** The trace's coverage status, passed through onto the result. */
  status?: LineageStatus;
  /** The entity id selected in the flow view (`?eid`), or null. */
  selectedEntityId: string | null;
  /**
   * The already-derived graph, when the caller has one.
   *
   * The view renders ONE graph and highlights it, so it holds the `deriveGraph`
   * result already; passing it in keeps the two from being derived twice per render
   * off the same inputs (and makes it impossible for the highlight to be computed
   * against a different node/edge set than the one on screen). Omitted, this
   * derives it — which is what the unit tests do, so a test's fixture is the graph
   * the highlight is computed against without restating it.
   */
  graph?: GraphSpec;
}

/**
 * Decide which nodes and edges to highlight for the selected entity, per THE
 * DEFINITION in this module's header.
 *
 * Composes with {@link deriveGraph}: the node and edge sets are its output, and
 * nothing here invents an element or re-derives a direction. Every id returned is
 * an id that graph actually contains, so a highlight can never name something the
 * reader cannot see.
 */
export function deriveLineageHighlight({
  entities,
  interactions,
  byLeg,
  status = null,
  selectedEntityId,
  graph,
}: DeriveLineageHighlightArgs): LineageHighlight {
  const spec = graph ?? deriveGraph(entities, interactions);

  // Nothing asked → nothing claimed. Note this is NOT the same as a selection that
  // resolves to no node: that is a stale `?eid` (an entity id from another trace,
  // or one the entities read has not returned), and it is treated the same way
  // here — no node to anchor the question on, so no answer — but it is reached
  // through the explicit lookup below so the two cases stay legible.
  if (selectedEntityId === null) return { ...NO_SELECTION, status };
  const selectedNodeId = spec.nodes.some((n) => n.id === selectedEntityId)
    ? selectedEntityId
    : null;
  if (selectedNodeId === null) return { ...NO_SELECTION, status };

  // THE INBOUND LEGS: every drawn edge delivering TO the selected entity (clause
  // 1). Read off `spec.edges`, so the per-leg direction is `flow.legDirection`'s
  // and not a second interpretation of it — and so a leg the graph could not draw
  // (unresolved participant) is not silently counted as a delivery the reader can
  // see. Those interactions are already disclosed by the graph's own `dropped`
  // notice, which the Lineage tab renders too.
  const inbound = spec.edges.filter((e) => e.target === selectedNodeId);

  // The natural-key bridge, built ONCE for the whole roll-up (see
  // lineageLabels.entityIdsByKey for why it is a map and what a collision does).
  const { byKey, ambiguous } = entityIdsByKey(entities);

  const sourceNodeIds = new Set<string>();
  const contributingEdgeIds: string[] = [];
  /** Unresolvable keys → how many contributing legs named each. */
  const unresolvedCounts = new Map<string, number>();
  let derivedLegs = 0;

  for (const edge of inbound) {
    // A PRESENT key with a `null` value and an ABSENT key are two different facts
    // about the wire (see `DataLineageByLeg`) and both mean "not yet derived", so
    // `byLeg.get` collapsing them here is safe and deliberate: neither may
    // contribute to the union, and neither counts as derived. The distinction is
    // preserved where it is actionable — on the leg's own Data lineage tab — and is
    // not something this roll-up can act on differently.
    const lineage: DataLineage | null | undefined = byLeg?.get(
      legLineageKey(edge.interactionId, edge.legType),
    );
    if (lineage == null) continue;

    derivedLegs += 1;
    // Derived is derived: an EMPTY `data_sources` contributes no node but the leg
    // is still a contributing leg and its arrow is still lit (clause 5). Pushed
    // before the source loop so that ordering cannot be mistaken for "lit only if
    // it produced a node".
    contributingEdgeIds.push(edge.id);

    for (const key of lineage.data_sources) {
      const id = byKey.get(key);
      if (id === undefined) {
        // Counted, not dropped: the view discloses it (see UnresolvedLineageSource).
        unresolvedCounts.set(key, (unresolvedCounts.get(key) ?? 0) + 1);
        continue;
      }
      // The selected entity may be among its own sources; that is kept, not
      // filtered (clause 4). `selectedNodeId` marks it separately so the reader can
      // still see which node they asked about.
      sourceNodeIds.add(id);
    }
  }

  // The three-way state. Order matters: "nothing delivers here" is checked FIRST,
  // because with no inbound legs there is no derivation to be pending on and
  // reporting `'pending'` would tell the reader to wait for an answer that will
  // never arrive — the same conflation `LineageCoverageAlert` refuses between "not
  // derived yet" and "the read failed".
  const state: LineageDerivationState =
    inbound.length === 0 ? 'no-inbound' : derivedLegs === 0 ? 'pending' : 'derived';

  return {
    selectedNodeId,
    // Sorted for a deterministic snapshot; the graph's own z-order is unaffected
    // (the view looks these up by id, it does not iterate them to draw).
    highlightedNodeIds: [...sourceNodeIds].sort(),
    // Already in `seq` order — inherited from `spec.edges`, which `deriveGraph`
    // sorts — so the list reads as the chronology of the deliveries.
    highlightedEdgeIds: contributingEdgeIds,
    // Sorted by key for the same determinism reason as the nodes; the count rides
    // along so the notice can say how much of the answer is missing.
    unresolved: [...unresolvedCounts.entries()]
      .map(([naturalKey, legCount]) => ({ naturalKey, legCount }))
      .sort((a, b) => (a.naturalKey < b.naturalKey ? -1 : a.naturalKey > b.naturalKey ? 1 : 0)),
    // Only the keys the roll-up could actually have tripped over are worth
    // reporting: an ambiguity on some unrelated entity's key is not a caveat on
    // THIS answer, and reporting it would be noise the reader cannot act on.
    ambiguousKeys: ambiguous.filter((k) => {
      const id = byKey.get(k);
      return id !== undefined && sourceNodeIds.has(id);
    }),
    state,
    inboundLegs: inbound.length,
    derivedLegs,
    status,
  };
}
