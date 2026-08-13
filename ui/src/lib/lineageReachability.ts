/**
 * Pure highlight derivation for the **Lineage** tab, from the SERVED **Lineage
 * reachability** reads (ADR-0028 D14, edge rule in D15).
 *
 * Two independent jobs, deliberately in one module because they paint one graph:
 *
 * 1. {@link deriveSourceHighlight} — which nodes are the trace's **data sources**,
 *    from `GET /api/traces/{tid}/data-lineage-summary`. A standing fact about the
 *    trace, shown with NO selection.
 * 2. {@link deriveReachabilityHighlight} — for a selected entity AND ONE CHOSEN
 *    SOURCE, its fan-in AND fan-out, from the two `data-lineage-graph` reads. The
 *    nodes *and the traversed legs*, because the route is what the endpoint returns
 *    the legs for.
 * 3. {@link resolveSourceChoice} — reconciling the reader's `?src` against the
 *    trace's actual source list, so a stale or absent choice is a *state* rather
 *    than a request nobody can answer.
 *
 * Render-free and unit-tested, like every `lib/` module here: jsdom cannot measure
 * an SVG, so the real coverage of this feature is `lineageReachability.test.ts`
 * rather than a render assertion about pixels.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHAT CHANGED, AND WHY THIS REPLACES THE CLIENT-SIDE ROLL-UP.
 *
 * `lineageGraph.ts` computed "the direct sources of the selected entity" from the
 * per-leg `byLeg` map, one hop, and REFUSED to walk transitively — because "a
 * transitive claim the backend never derived would be the UI inventing lineage".
 * That reasoning was correct, and ADR-0028 D15 records that it is exactly what the
 * server now removes: **the backend derives the multi-hop claim, so it is
 * citable.** This module therefore renders a SERVED answer rather than composing
 * one, and the client-side roll-up is deleted rather than kept beside it — two
 * competing notions of "the lineage highlight" is the duplication that lets two
 * tabs disagree about what is true.
 *
 * The consequences of that swap, each of which is a thing this module must NOT
 * re-derive:
 *
 * - **The edge rule is the server's.** A hop `A → B` exists iff the trace has a
 *   leg whose *per-leg* direction runs A→B AND that leg has a derived lineage row.
 *   Nothing here re-reads the parent **Interaction**'s `caller → callee`: a
 *   response leg runs callee → caller, and an agent's data mostly *arrives* as the
 *   responses to its own calls (ADR-0025), so that reading would drop most real
 *   inbound flow. The served `legs` already point the way the walk went.
 * - **Multi-hop is now in scope**, with `hops` as the distance. `lineageGraph`'s
 *   clause 3 forbade composing it client-side; reading it off the response is not
 *   composing it.
 * - **The walk is SOURCE-RELATIVE, not merely direction-relative.**
 *   `docs/data_lineage_alg.md` specifies the read as `fanout(entity, source)` /
 *   `fanin(entity, source)`: an edge is traversed only if the chosen source is in
 *   that leg's lineage `data_sources`. So the question is "trace THIS source's data
 *   through this entity", not "everything reachable from this entity", and the same
 *   entity has a *different* answer per source. An earlier draft of this module
 *   documented the read as taking only a direction; that is now false and is
 *   corrected here rather than left standing.
 * - **The tri-state moved but did not soften.** `state` is the server's
 *   `derived` / `pending` / `no-adjacent`, and `pending_frontier` names the
 *   entities the walk could not continue through *yet*. An empty `entities` list
 *   is NOT "nothing flowed" — that is the whole reason those two fields exist.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THE THREE AXES, AND WHY THEY ARE NOT ONE ENUM.
 *
 * A node can be several things AT ONCE, and the reader must be able to see each:
 *
 *   - a **trace data source** (always-on, selection-independent);
 *   - **upstream** of the selection (fan-in), at some hop distance;
 *   - **downstream** of it (fan-out), at some hop distance.
 *
 * These genuinely co-occur. A source is very often also upstream of whatever you
 * clicked. More surprisingly, an entity is routinely BOTH upstream and downstream
 * of the selection — `agent → tool → agent` is the ordinary shape of every tool
 * call, so the tool's request leg puts it downstream and its response leg puts it
 * upstream (ADR-0028 D15's cycle note). Against the real endpoint this is not an
 * edge case but the common case: on a live 25-interaction trace, fan-in and
 * fan-out of a leaf tool each returned all ten entities.
 *
 * A single-valued role would have to pick a winner and silently discard the rest,
 * which is why {@link ReachabilityHighlight} returns SEPARATE id sets and the
 * renderer composes independent classes from them. Collapsing "upstream" and
 * "downstream" into one "related" set was considered and REJECTED for the same
 * reason: they are opposite claims about who gave data to whom, and a governance
 * reader who cannot tell them apart has been told nothing useful. Blending them
 * into one undifferentiated blob is the failure mode this shape prevents.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHAT THIS MODULE DOES NOT DO.
 *
 * It does not walk, prune, order or intersect anything. Every id it returns came
 * from a response, and its whole job is translation: natural key → entity id (for
 * the summary), and `(interaction_id, leg_type)` → drawn edge id (for the route).
 * A large fanout is passed through unedited even though it may merely reflect a
 * trivial matcher rather than thorough tracing (D14) — trimming it here would be
 * this module deciding what the server's answer really meant.
 */
import type { GraphSpec } from './graph';
import { entityIdsByKey } from './lineageLabels';
import type { Entity } from './flow';
import type {
  LineageDirection,
  LineageReachability,
  LineageReachabilityState,
  LineageSummary,
} from '../types';

/**
 * A lineage fact the graph CANNOT draw, because the id or key it names matches no
 * node in this trace.
 *
 * It happens legitimately: `list sources` cites origins by natural key and the
 * derivation can name one outside the trace's own entity set (an upstream service
 * the trace never spanned), or the entities read may simply be behind. Either way
 * the fact is REAL and the graph is silent about it — the silent under-report that
 * `lib/graph`'s `dropped` exists to prevent — so it is returned for the view to
 * disclose rather than dropped.
 */
export interface UnresolvedLineageRef {
  /** The natural key (summary) or entity id (frontier) that resolved to no node. */
  ref: string;
}

/**
 * Which nodes are the trace's **data sources** — the always-on half of the tab.
 *
 * Note what this is NOT: "entities whose kind is declared a source" in the
 * taxonomy. `sources` is the **union of the derived `data_sources`** over the
 * trace's legs (ADR-0028 D14), i.e. the origins lineage actually attributed
 * content to. The two sets diverge exactly where a delegation-shaped tool
 * over-reports, and substituting the taxonomy read is the mistake the ADR names.
 */
export interface SourceHighlight {
  /**
   * Node ids that are trace data sources. Sorted for a deterministic snapshot.
   *
   * A source that resolves to a node in the trace lands here; one that does not
   * lands in {@link unresolved} and is disclosed instead. Never both.
   */
  sourceNodeIds: string[];
  /**
   * Sources whose natural key matched no entity in this trace. Disclosed by the
   * view, never silently dropped — each is a real origin the picture omits, so the
   * lit nodes are not the full set.
   */
  unresolved: UnresolvedLineageRef[];
  /**
   * Natural keys claimed by MORE than one entity, so the view can say a lit node
   * is an arbitrary (if deterministic) pick among them. See
   * `lineageLabels.entityIdsByKey`. Expected empty against a sane server.
   *
   * Filtered to keys that actually contributed a lit node: an ambiguity on some
   * unrelated entity's key is not a caveat on THIS answer, and reporting it would
   * be noise the reader cannot act on.
   */
  ambiguousKeys: string[];
  /**
   * How many sources the summary named in total, resolvable or not.
   *
   * Carried so the view can say "6 of 8 shown" rather than only showing 6. A
   * `sourceNodeIds.length` alone cannot distinguish "the trace has six sources"
   * from "the trace has eight and we could draw six".
   */
  totalSources: number;
  /**
   * The node id of the CHOSEN source — the one whose data the current answer traces
   * — or `null` when nothing is chosen or its key resolves to no drawn node.
   *
   * A SUBSET of {@link sourceNodeIds}, never a replacement for it. Colouring every
   * source is a standing fact about the trace and stays unconditional (ADR-0028 D14);
   * this is the separate claim "…and THIS one is the subject of the highlight you are
   * looking at". Both must be visible at once: a reader has to be able to see that
   * the trace has five origins AND which of the five this graph is about, and a
   * treatment that lit only the chosen one would silently shrink the trace's source
   * set every time somebody picked one.
   *
   * `null` for an unresolvable chosen key is not a silent drop — the key is already
   * in {@link unresolved}, and the view names the choice in the control and in its
   * own notice regardless of whether a node could be marked.
   */
  chosenSourceNodeId: string | null;
}

/** The empty source answer — for a trace with no roll-up, and for a failed read. */
const NO_SOURCES: SourceHighlight = {
  sourceNodeIds: [],
  unresolved: [],
  ambiguousKeys: [],
  totalSources: 0,
  chosenSourceNodeId: null,
};

/**
 * Resolve the trace's `list sources` natural keys to node ids.
 *
 * `summary` is `undefined` while the read is in flight or after it failed; both
 * yield the empty highlight, and NEITHER may be rendered as "this trace has no
 * data sources" — the view distinguishes them by the query's own state, which is
 * the same split `LineageState` makes (a failed read is not an empty answer). This
 * function cannot tell them apart and deliberately does not try.
 */
export function deriveSourceHighlight({
  entities,
  summary,
  graph,
  chosenSource = null,
}: {
  entities: readonly Entity[];
  summary: LineageSummary | undefined;
  /** The rendered graph, so a lit id is always an id the reader can actually see. */
  graph: GraphSpec;
  /**
   * The source natural key the reader is tracing ({@link SourceChoice.source}), or
   * `null`. Resolved to {@link SourceHighlight.chosenSourceNodeId} through the SAME
   * key bridge every other source goes through, so the chosen node can never be a
   * node that is not also in `sourceNodeIds`.
   */
  chosenSource?: string | null;
}): SourceHighlight {
  if (!summary || summary.sources.length === 0) return NO_SOURCES;

  // The natural-key bridge, reused rather than re-implemented: lineage cites
  // natural keys and graph nodes are keyed on entity id, and `entityIdsByKey`
  // already owns that mapping including what a collision does (first wins, key
  // reported). A second copy here would be the duplication the house rules forbid.
  const { byKey, ambiguous } = entityIdsByKey(entities);
  const drawable = new Set(graph.nodes.map((n) => n.id));

  const sourceNodeIds = new Set<string>();
  const unresolved: UnresolvedLineageRef[] = [];

  for (const key of summary.sources) {
    const id = byKey.get(key);
    // Two distinct ways a source fails to resolve, folded here on purpose because
    // the view's disclosure is the same for both: the key names no entity at all,
    // or it names one the GRAPH did not draw (an interaction with an unresolved
    // participant — already disclosed by `graph.dropped`). In each case there is no
    // node to light, and claiming otherwise would point the reader at nothing.
    if (id === undefined || !drawable.has(id)) {
      unresolved.push({ ref: key });
      continue;
    }
    sourceNodeIds.add(id);
  }

  // Resolved through the same `byKey` bridge and then checked against the LIT set
  // rather than merely against `drawable`: a chosen key that named a real entity the
  // graph did not draw is already in `unresolved`, and reporting it here as well
  // would let the view mark a node that is not one of the trace's marked sources.
  const chosenId = chosenSource === null ? undefined : byKey.get(chosenSource);
  return {
    sourceNodeIds: [...sourceNodeIds].sort(),
    unresolved,
    ambiguousKeys: ambiguous.filter((k) => {
      const id = byKey.get(k);
      return id !== undefined && sourceNodeIds.has(id);
    }),
    totalSources: summary.sources.length,
    chosenSourceNodeId: chosenId !== undefined && sourceNodeIds.has(chosenId) ? chosenId : null,
  };
}

/**
 * WHY THE READER PICKS EXACTLY ONE SOURCE, AND WHY THE UI MUST NOT UNION THEM.
 *
 * `docs/data_lineage_alg.md`'s `## deferred issues` says it outright: "Lineage
 * fanout/fanin Given multiple sources - semantics are not clear: Do we expect the
 * exact set of sources? Any of them?" With that question open upstream, a UI that
 * offered "all sources" would have to *choose* an answer — ANY-of (a union of
 * per-source walks) or ALL-of (an intersection) — and then present its choice as
 * though the server had derived it. Those two produce different graphs on the same
 * trace, and a governance reader has no way to tell which they are looking at.
 *
 * So: exactly one source at a time, always named on screen. **Do not "improve" this
 * into a multi-select union.** It is not a missing feature, it is a deliberate
 * refusal to invent semantics the algorithm has not settled.
 */
export type SourceChoiceState =
  /** The reader has not chosen yet. An INSTRUCTION state, not an empty result. */
  | 'unchosen'
  /** The trace's roll-up named no sources at all — nothing derived to trace. */
  | 'no-sources'
  /**
   * A choice was supplied but this trace's roll-up does not contain it — a stale
   * `?src` from another trace, a hand-edited URL, or a source that has since
   * dropped out of the roll-up. Treated as unchosen for the purposes of asking (a
   * question about a source this trace has no lineage for cannot be answered), but
   * kept DISTINCT from `'unchosen'` so the view can say why the bookmark did not
   * restore instead of silently blanking it.
   */
  | 'stale'
  /** A real source of this trace is chosen. `source` is non-null exactly here. */
  | 'chosen';

/** The reader's source choice, reconciled against what the trace actually offers. */
export interface SourceChoice {
  state: SourceChoiceState;
  /**
   * The source natural key to ASK WITH, or `null` when there is nothing askable.
   *
   * Non-null iff `state === 'chosen'`. Every caller gates its query on this one
   * field, which is what stops a `'stale'` or `'no-sources'` trace from firing a
   * request the server would 400 — the source parameter is required.
   */
  source: string | null;
  /** The value the reader supplied, even when it did not resolve. For the notice. */
  requested: string | null;
  /** The trace's selectable sources, in the roll-up's own order. */
  available: readonly string[];
}

/**
 * Reconcile a requested source (the `?src` URL param) against the trace's roll-up.
 *
 * PURE AND HERE, not inline in the view, for this module's standing reason: it
 * decides whether a query fires at all, and "did the read fire?" is exactly the kind
 * of thing a render test cannot honestly assert about an SVG-bearing component. The
 * four states it returns are what the view branches on.
 *
 * NO AUTO-PICK OF THE FIRST SOURCE, and this was the tempting alternative. It would
 * make the tab show an answer immediately, which is precisely the problem: the
 * reader would be looking at a highlighted graph that answers a question they never
 * asked, about whichever source happened to sort first. On a governance surface an
 * unrequested claim is worse than a prompt. `'unchosen'` is therefore a real state
 * the view renders as an instruction.
 *
 * `summary` is `undefined` while the read is in flight or after it failed — the same
 * ambiguity {@link deriveSourceHighlight} documents. Both yield `'unchosen'` with
 * `available: []`, and NEITHER may be rendered as "this trace has no sources": the
 * view distinguishes them by the query's own state, exactly as it already does for
 * the source colouring. Note this means `'no-sources'` is only ever returned for a
 * summary that actually landed and actually named nothing.
 */
export function resolveSourceChoice({
  requested,
  summary,
}: {
  /** The `?src` param, or `null` when absent. */
  requested: string | null | undefined;
  summary: LineageSummary | undefined;
}): SourceChoice {
  const available = summary?.sources ?? [];
  const req = requested ?? null;
  // In flight / failed: `available` is empty but that is not a claim about the
  // trace, so this cannot be `'no-sources'`. A requested value is held in
  // `requested` and re-reconciled once the summary lands — which is what makes a
  // bookmarked `?src` restore rather than being judged stale against no data.
  if (!summary) return { state: 'unchosen', source: null, requested: req, available };
  if (available.length === 0) {
    return { state: 'no-sources', source: null, requested: req, available };
  }
  if (req === null) return { state: 'unchosen', source: null, requested: null, available };
  if (!available.includes(req)) {
    return { state: 'stale', source: null, requested: req, available };
  }
  return { state: 'chosen', source: req, requested: req, available };
}

/**
 * One direction's contribution to the highlight, kept separate from the other's.
 *
 * Two of these, never merged, because fan-in and fan-out are opposite claims about
 * who gave data to whom (see THE THREE AXES). Each carries its own `state` and
 * `isError` so one direction failing cannot erase the other's good answer.
 */
export interface DirectionHighlight {
  direction: LineageDirection;
  /** Reached node ids, sorted. Excludes the seed unless the flow genuinely returns to it. */
  nodeIds: string[];
  /** Traversed leg ids (`<interaction>:<leg_type>`) — THE ROUTE. In the response's order. */
  edgeIds: string[];
  /**
   * `node id → fewest hops from the seed`, for a graded treatment (nearer =
   * stronger).
   *
   * A DISTANCE, not an ordering: two entities at the same depth were reached by
   * different routes and the lineage algebra has no truthful interleaving to offer
   * (ADR-0028 D10). So it may scale an emphasis and may not be drawn as a sequence.
   */
  hopsByNodeId: Map<string, number>;
  /** The server's three-valued verdict. Read this BEFORE `nodeIds`. */
  state: LineageReachabilityState;
  /**
   * Entities the walk reached but could NOT continue through yet, resolved to node
   * ids. "We don't know yet", never "there is nothing".
   */
  pendingFrontierNodeIds: string[];
  /**
   * Frontier entity ids that match no drawn node, disclosed rather than dropped —
   * same reasoning as {@link SourceHighlight.unresolved}.
   */
  unresolvedFrontier: UnresolvedLineageRef[];
  /**
   * A walk bound was hit, so the answer is a PREFIX of the real reachable set.
   *
   * A separate fact from the frontier and must not be merged with it (ADR-0028
   * D15): the frontier means *not derived yet, ask again later*, this means
   * *derived, but this answer declined to return it all*. Only a wider bound
   * produces the rest, so telling a reader to wait would be a lie.
   */
  truncated: boolean;
  /**
   * This direction's read FAILED — a fourth state, separate from the three the
   * server spells. Nothing is known, which is not the same as `no-adjacent`'s
   * "nothing is there": one says retry, the other says the question is answered.
   */
  isError: boolean;
  /** Still loading. Distinct from an empty answer for the same reason. */
  isLoading: boolean;
}

/** The whole selection-driven answer: both directions, plus what to dim. */
export interface ReachabilityHighlight {
  /**
   * The source natural key this answer is ABOUT, or `null` when none is chosen.
   *
   * Carried on the answer rather than only in the view's own state because the
   * highlight is meaningless without it: "these entities are upstream" is not a
   * claim, "these entities are upstream *of this source's data*" is. A view that
   * held the two separately could paint one source's highlight under another
   * source's label during the instant between a re-select and its response landing.
   */
  source: string | null;
  /**
   * The selected entity's own node id, or `null` when nothing is selected / the
   * selection names no node in this trace.
   *
   * Carried separately so the view can mark "this is the one you asked about"
   * distinctly from "this is where its data came from / went" — and so an entity
   * that is genuinely both (a cycle returning to the seed) reads as both rather
   * than being indistinguishable from an ordinary neighbour.
   */
  selectedNodeId: string | null;
  /** Upstream / ancestors. */
  fanin: DirectionHighlight;
  /** Downstream / descendants. */
  fanout: DirectionHighlight;
  /**
   * Every node id in either direction's answer, plus the seed — the set that stays
   * at full strength while everything else dims.
   *
   * Precomputed here rather than unioned in the renderer so the two cannot drift:
   * a node lit by one direction and dimmed by the other would flicker on which set
   * the renderer happened to test first.
   */
  litNodeIds: string[];
  /** Every traversed leg id in either direction. The union of both routes. */
  litEdgeIds: string[];
  /**
   * True when SOMETHING is on screen to explain — either direction produced a node,
   * an edge, or a frontier.
   *
   * Not `state === 'derived'`: a direction can be `derived` with an empty node list
   * (see the server's note) and a `pending` one still has a frontier worth drawing.
   * The view uses this only to decide whether dimming the rest is informative; it
   * never substitutes for reading each direction's `state`.
   */
  hasAnswer: boolean;
}

/** One direction's empty answer, in whichever of the four states applies. */
function emptyDirection(
  direction: LineageDirection,
  { isError = false, isLoading = false }: { isError?: boolean; isLoading?: boolean } = {},
): DirectionHighlight {
  return {
    direction,
    nodeIds: [],
    edgeIds: [],
    hopsByNodeId: new Map(),
    // `'no-adjacent'` is the non-claiming value here ONLY because the caller pairs
    // it with `isError` / `isLoading`, which the view reads first. On its own it
    // would be an assertion that the trace has nothing adjacent, which a failed or
    // in-flight read has no standing to make.
    state: 'no-adjacent',
    pendingFrontierNodeIds: [],
    unresolvedFrontier: [],
    truncated: false,
    isError,
    isLoading,
  };
}

/**
 * Map one direction's response onto drawable ids.
 *
 * The edge-id translation is the only arithmetic in this module and it is not a
 * guess: a traversed leg is `(interaction_id, leg_type)` and `lib/graph` keys an
 * edge as `` `${interactionId}:${legType}` `` — the same canonical leg grain
 * `flow.legLineageKey` uses. So the join is the identity, not a parse. A leg whose
 * edge the graph did not draw is skipped rather than invented; the graph's own
 * `dropped` notice already accounts for those interactions.
 */
function deriveDirection(
  direction: LineageDirection,
  response: LineageReachability | undefined,
  {
    isError,
    isLoading,
    drawableNodes,
    drawableEdges,
  }: {
    isError: boolean;
    isLoading: boolean;
    drawableNodes: ReadonlySet<string>;
    drawableEdges: ReadonlySet<string>;
  },
): DirectionHighlight {
  if (isError || isLoading || !response) {
    return emptyDirection(direction, { isError, isLoading });
  }

  const hopsByNodeId = new Map<string, number>();
  for (const e of response.entities) {
    // The walk reached it, so it is part of the answer — but only a DRAWN node can
    // be lit. An entity the graph has no node for is not counted as lit and not
    // reported as unresolved either: the graph's `dropped`/isolated notices already
    // explain why a node is missing, and a second notice saying the same thing in
    // different words is noise.
    if (!drawableNodes.has(e.id)) continue;
    // First arrival wins on a tie, but the server already sends the FEWEST hops
    // per entity and each id once, so `Math.min` is belt-and-braces against a
    // duplicate rather than a semantic choice.
    const prior = hopsByNodeId.get(e.id);
    hopsByNodeId.set(e.id, prior === undefined ? e.hops : Math.min(prior, e.hops));
  }

  const edgeIds: string[] = [];
  const seenEdges = new Set<string>();
  for (const leg of response.legs) {
    const id = `${leg.interaction_id}:${leg.leg_type}`;
    if (!drawableEdges.has(id) || seenEdges.has(id)) continue;
    seenEdges.add(id);
    edgeIds.push(id);
  }

  const pendingFrontierNodeIds: string[] = [];
  const unresolvedFrontier: UnresolvedLineageRef[] = [];
  for (const id of response.pending_frontier) {
    // The frontier is entity IDS (not natural keys — the summary's sources are the
    // ones that need the key bridge), so no translation is needed, only a
    // drawability check.
    if (drawableNodes.has(id)) pendingFrontierNodeIds.push(id);
    else unresolvedFrontier.push({ ref: id });
  }

  return {
    direction,
    nodeIds: [...hopsByNodeId.keys()].sort(),
    edgeIds,
    hopsByNodeId,
    // The server's verdict, passed through UNTOUCHED. Not recomputed from whether
    // `nodeIds` came out empty — that inference is precisely the collapse `state`
    // exists to prevent, and it would additionally be wrong here, since a node can
    // drop out for being undrawable without changing what the server derived.
    state: response.state,
    pendingFrontierNodeIds: pendingFrontierNodeIds.sort(),
    unresolvedFrontier,
    truncated: response.truncated,
    isError: false,
    isLoading: false,
  };
}

/**
 * Build the selection-driven highlight from BOTH directions' reads.
 *
 * Both are shown at once (see {@link ReachabilityHighlight}) rather than behind a
 * toggle. A toggle was considered and REJECTED: the two answers are halves of one
 * question — "what touched this entity's data" — and making the reader flip back
 * and forth to assemble it puts the burden of holding half the graph in their head
 * on them, while also hiding the most governance-relevant shape (an entity that is
 * both upstream and downstream, i.e. data that came back). They are kept
 * DISTINGUISHABLE instead, by separate id sets the renderer styles differently, so
 * showing both does not blend them into one undifferentiated set.
 *
 * TWO THINGS ARE NOW REQUIRED BEFORE THERE IS AN ANSWER, not one: a seed entity AND
 * a chosen source. `source === null` short-circuits to the same non-claiming empty
 * highlight `selectedEntityId === null` does — deliberately, because a walk with no
 * source is not a weaker question, it is not a question at all (the parameter is
 * required, so the request cannot even be made). Note the two are NOT collapsed into
 * one "not ready" flag: the VIEW words them differently ("select an entity" vs
 * "choose a data source"), so it reads them separately off {@link SourceChoice} and
 * `selectedNodeId`.
 */
export function deriveReachabilityHighlight({
  selectedEntityId,
  source,
  fanin,
  fanout,
  graph,
}: {
  /** The flow view's `?eid` selection. THE one notion of "selected entity". */
  selectedEntityId: string | null;
  /**
   * The chosen data source natural key (`SourceChoice.source`), or `null`.
   *
   * A natural KEY, not an entity id, because that is what lineage stores and what
   * the endpoint's `source` parameter takes (ADR-0028 / `lineageLabels`' header). It
   * is deliberately NOT resolved to a node here: this function's job is to say what
   * the answer covers, and the chosen source's own node is already lit by
   * {@link deriveSourceHighlight}'s always-on set.
   */
  source: string | null;
  fanin: {
    data: LineageReachability | undefined;
    isError: boolean;
    isLoading: boolean;
  };
  fanout: {
    data: LineageReachability | undefined;
    isError: boolean;
    isLoading: boolean;
  };
  graph: GraphSpec;
}): ReachabilityHighlight {
  const drawableNodes = new Set(graph.nodes.map((n) => n.id));
  const drawableEdges = new Set(graph.edges.map((e) => e.id));

  // Nothing asked → nothing claimed. A selection that resolves to no node (a stale
  // `?eid` from another trace, or an entity the read has not returned) is treated
  // the same way, but reached through the explicit lookup so the two stay legible.
  const selectedNodeId =
    selectedEntityId !== null && drawableNodes.has(selectedEntityId) ? selectedEntityId : null;
  // NO SOURCE IS THE SAME NON-CLAIM AS NO SEED. The reads are gated on both (see
  // `useLineageGraph`'s `enabled`), so with either missing `fanin.data`/`fanout.data`
  // are `undefined` and every direction would come out empty anyway — but returning
  // the explicit non-claiming shape here means `selectedNodeId` also stays `null`,
  // which is what `roleOf` keys on to dim NOTHING. Without this arm, choosing an
  // entity but no source would dim the whole graph while claiming nothing, i.e. paint
  // "not part of the answer" over a question that was never asked.
  if (selectedNodeId === null || source === null) {
    return {
      source,
      selectedNodeId: null,
      fanin: emptyDirection('fanin'),
      fanout: emptyDirection('fanout'),
      litNodeIds: [],
      litEdgeIds: [],
      hasAnswer: false,
    };
  }

  const inHighlight = deriveDirection('fanin', fanin.data, {
    isError: fanin.isError,
    isLoading: fanin.isLoading,
    drawableNodes,
    drawableEdges,
  });
  const outHighlight = deriveDirection('fanout', fanout.data, {
    isError: fanout.isError,
    isLoading: fanout.isLoading,
    drawableNodes,
    drawableEdges,
  });

  // The seed is always lit: it is the subject of the question, and dimming the node
  // the reader just clicked would be absurd.
  const litNodes = new Set<string>([selectedNodeId, ...inHighlight.nodeIds, ...outHighlight.nodeIds]);
  const litEdges = new Set<string>([...inHighlight.edgeIds, ...outHighlight.edgeIds]);

  return {
    source,
    selectedNodeId,
    fanin: inHighlight,
    fanout: outHighlight,
    litNodeIds: [...litNodes].sort(),
    // Sorted so the union of two response-ordered lists is deterministic; the
    // renderer looks these up by id and does not iterate them to draw, so losing
    // the response's `seq` order here costs nothing (each direction's own
    // `edgeIds` keeps it).
    litEdgeIds: [...litEdges].sort(),
    hasAnswer:
      inHighlight.nodeIds.length > 0 ||
      outHighlight.nodeIds.length > 0 ||
      inHighlight.edgeIds.length > 0 ||
      outHighlight.edgeIds.length > 0 ||
      inHighlight.pendingFrontierNodeIds.length > 0 ||
      outHighlight.pendingFrontierNodeIds.length > 0,
  };
}
