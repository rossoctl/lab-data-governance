/**
 * Pure node/edge derivation for the Execution Flow (graph) view.
 *
 * Sibling to `./flow.ts`: that module holds the flat-leg / depth derivations the
 * tables need, this one holds the entity→node and leg→edge derivations the
 * topology graph needs. Both are deliberately render-free so the shape of the
 * graph is unit-testable without laying out an SVG — jsdom cannot measure one, so
 * *this* is where the real coverage lives (see graph.test.ts).
 *
 * The single source for the derivation; the component only maps these rows onto
 * PatternFly topology models.
 *
 * ONE EDGE PER **LEG**, NOT PER INTERACTION. The graph draws the same rows the
 * Flat view lists, in the same order: it consumes `flatLegRows` directly rather
 * than re-walking `interactions`, so the two views can never disagree about what
 * a leg is or what order legs happened in. A completed interaction therefore
 * contributes TWO edges pointing opposite ways — `A→B` at the request's `seq` and
 * `B→A` at the response's — because that is what actually happened on the wire
 * (ADR-0025), and a single caller→callee arrow silently asserted that a response
 * either did not exist or did not travel.
 *
 * THE LAYOUT LIVES HERE TOO, as a (column, row) pair per node rather than as
 * pixels — see `GraphNodeSpec.column` / `.row` and `assignColumns`. It is a
 * LAYERED (Sugiyama-style) assignment: column = call depth along request legs,
 * row = chronological order within the column. That is derivation, not rendering:
 * "how deep in the call tree is this entity, and which of its siblings came
 * first" are facts about the trace, testable as pure logic, whereas the px step
 * between columns is the component's business. jsdom cannot measure an SVG, so
 * anything left in the component is effectively uncovered — which is the reason
 * this split is the repo's rule and not a preference.
 */
import { flatLegRows, legDirection, type Entity, type Interaction } from './flow';

/** One graph node: an **Entity**, positioned by the layout not by us. */
export interface GraphNodeSpec {
  /** The entity's id — also the node id the edges' source/target refer to. */
  id: string;
  /** The visible label: the entity's `display_name` (falling back to its id). */
  label: string;
  /** The entity's kind, which drives the node's color (see kindColorToken). */
  kind: string;
  /** The entity's natural key, for the node's hover title. */
  naturalKey: string;
  /**
   * True when NO interaction in this trace names this entity as caller or
   * callee. EDGE CASE (isolated node): the entity was derived from a span but
   * never participated in a resolved interaction. It is still rendered — an
   * entity that exists is a governance fact, and dropping it would understate
   * the trace's participant set — but it is flagged so the view can say so.
   */
  isIsolated: boolean;
  /**
   * This entity's slot in **first-encounter order**: 0 for the first entity the
   * trace touches, 1 for the next newly-seen one, and so on. Dense and
   * contiguous over `nodes` (`0 … nodes.length - 1`), so a renderer can multiply
   * it by a step and get a deterministic position without a second pass.
   *
   * WHY THIS IS DERIVATION AND NOT LAYOUT. The ORDER is a fact about the trace —
   * "who did the trace touch, and when did each first appear" — and it is
   * exactly as testable as `seq` is. The pixels are a rendering choice. Keeping
   * the order here means the graph and any future view of it read the same
   * walk, and means the walk is covered by the pure tests in graph.test.ts
   * rather than by something that would have to measure an SVG.
   *
   * THE WALK. The edges (already sorted by the trace-wide leg `seq` — see
   * `edges`) are visited in order and each edge contributes its `source` then
   * its `target`; the first sighting of an id claims the next free slot. Source
   * before target within one edge because that is the direction the leg
   * travelled: the caller of the first request is the entity the trace started
   * at. An entity whose first appearance is as a TARGET therefore gets its slot
   * at that moment, not earlier — there is nothing earlier to give it.
   *
   * WHY THE EDGES AND NOT THE INTERACTIONS. An interaction with an unresolved
   * participant is not drawable and contributes no edge, so walking
   * `interactions` would assign slots to positions the graph never draws and
   * leave gaps in the chronology. Walking the drawn edges keeps the ordering and
   * the picture in agreement by construction.
   *
   * ISOLATED ENTITIES SORT LAST, and among themselves in `entities` order. No
   * leg names them, so the trace never "encountered" them and any slot in the
   * middle of the sequence would be a fabricated claim about when they appeared.
   * Note the tie-break matters: `entities` order is the API's order, which is
   * stable for a trace, so a reload redraws the identical picture — which is also
   * what makes their `row` deterministic in the trailing column they are parked
   * in (see `deriveGraph`).
   *
   * NOT the same thing as `isIsolated`'s negation, either: an entity named only
   * by a DROPPED interaction is not isolated (it demonstrably participated) but
   * contributes no edge, so it too has no encounter and sorts with the trailing
   * group.
   *
   * STILL LIVE after the move to a layered layout, in a NARROWER role: it is no
   * longer the whole position (that was the diagonal staircase, which is what put
   * every node on one line and drew every skipping edge through its neighbours).
   * It is now the CHRONOLOGY, and the layout consumes it as the within-column
   * ordering key — see {@link GraphNodeSpec.row}.
   */
  encounterIndex: number;
  /**
   * The node's **column**: its call depth, counted along the REQUEST direction
   * from a root caller. `0` for an entity nothing ever calls (the trace's
   * entry points), `1` for what those call, and so on. Left-to-right in the
   * render, so "A calls B" draws A left of B — the user's own statement of what
   * makes a flow readable.
   *
   * REQUEST LEGS ONLY. A response travels callee → caller, backwards along the
   * call direction, so counting it as depth would push each callee one column
   * further right than its caller AND then push the caller further right than
   * that — every completed interaction would ratchet the pair rightwards forever
   * and the columns would encode round-trip count instead of call depth. The
   * request direction IS the call direction, so it is the only one that defines
   * depth. (`legDirection` already gives a request leg as caller → callee, so the
   * request edges are exactly the call graph.)
   *
   * LONGEST PATH, NOT SHORTEST. If A calls B directly and also calls C which
   * calls B, B belongs to the right of C or the A→B edge would skip a column
   * while the C→B edge went backwards. Taking the maximum over all incoming
   * request paths is what keeps every request edge pointing strictly rightwards
   * wherever the graph allows it.
   *
   * CYCLES TERMINATE. A→B→A is not a cycle here (the return trip is a response
   * leg, which is excluded), but a genuine request cycle — A requests B, B
   * requests A — is possible and so is a self-request. The relaxation below is
   * bounded by node count rather than run to a fixed point, so a cycle can never
   * spin: see `assignColumns`.
   */
  column: number;
  /**
   * The node's **row** within its column: `0` for the topmost, `1` for the next
   * down, dense within each column.
   *
   * ORDERED BY `encounterIndex`, which is the trace's chronology. This is the
   * user's example made a guarantee: if A calls B and LATER calls C, then B and C
   * are both at depth 1 — the same column — and B was encountered first, so B is
   * row 0 and C is row 1. B is ABOVE C. Reading down a column is therefore
   * reading forwards in time, and reading across is following the calls.
   *
   * Rows are per-column, so two nodes in DIFFERENT columns may share a row
   * number; a row number is only comparable within one column.
   */
  row: number;
}

/**
 * One graph edge: an **Interaction leg**, drawn in that leg's own direction.
 *
 * A request leg is `caller → callee`; a response leg is `callee → caller`. The
 * swap is not decided here — it comes from `flow.legDirection`, shared with the
 * Flat table's Caller/Callee columns.
 */
export interface GraphEdgeSpec {
  /**
   * `<interaction id>:<leg_type>` — the canonical **Interaction leg** key, the
   * grain at which a leg is unique (the same grain `flow.legLineageKey` uses).
   * The interaction id alone is NOT unique any more: its two legs are two edges,
   * and a topology model with duplicate edge ids silently drops one.
   */
  id: string;
  /** The interaction this leg belongs to, so an edge maps back to a table row. */
  interactionId: string;
  /** Which half of the interaction this edge is. */
  legType: 'request' | 'response';
  /** The leg's origin for THIS leg's direction. Never null (see dropped). */
  source: string;
  /** The leg's destination for THIS leg's direction. Never null (see dropped). */
  target: string;
  /**
   * The edge's visible label: this leg's `seq`, as a string. A leg has exactly
   * one `seq` (the trace-wide leg ordering the Flat view sorts on), so the label
   * is a single number and stays legible on a short arrow — which is the reason
   * the interaction's prose `summary` moved to `title` instead of being the
   * label.
   *
   * That rejection was of PROSE specifically, not of every alternative to the
   * seq number, and it's narrower than it reads: what a short arrow cannot
   * carry is an unbounded *sentence*, not a bounded set of short uppercase
   * tokens. The risk trace view (issue #170 follow-up) replaces this field's
   * rendered text with an interaction's regulatory tags — but does so at the
   * RENDERER (`ExecutionFlowGraph.tsx`'s `EntityGraph`), not here: this field
   * is still always the `seq` coming out of `deriveGraph`, unconditionally.
   * The renderer swaps in the tag string via a side-channel
   * `classificationByInteraction` map, the same shape `EntityGraphProps.riskLevelByInteraction`
   * already uses for risk colour — deliberately, since this derivation has no
   * access to classification data at all (it isn't part of `flow.Interaction`;
   * see `riskForestAdapter.ts`'s header for why a field wasn't added there
   * either). Don't add a `tags` field here to "do it properly" — a consumer
   * on the other two tabs would just find it permanently `undefined`.
   */
  label: string;
  /** The leg's `seq` itself, for ordering and for tests that assert on a number. */
  seq: number;
  /** The interaction's `summary` (falling back to its id), for the tooltip. */
  title: string;
  /**
   * True only when THIS LEG's own `error` is exactly `true`.
   *
   * Deliberately the leg's error, not the interaction's `any_error`: an edge
   * represents one leg, so it must show that leg's outcome. `any_error` is an
   * OR-aggregate across both legs, so using it would paint a successful request
   * arrow red merely because the response later failed — reporting a failure at a
   * point in the trace where none had happened yet.
   *
   * Tri-state is preserved: `leg.error` is `boolean | null`, and `null` means
   * "not yet aggregated", NOT "succeeded" and NOT "failed". Only an explicit
   * `true` colours the edge, the same never-render-unknown-as-a-verdict rule the
   * interaction-level check followed and the lineage status follows.
   */
  isError: boolean;
  /**
   * EDGE CASE (self-call): caller === callee. The interaction is a genuine
   * self-call (an agent recursing, a tool re-entering itself), so it is kept as
   * a real edge from the node back to itself rather than dropped. Flagged
   * because a renderer may want a distinct treatment — a straight
   * source-equals-target line is invisible.
   *
   * A self-call's RESPONSE leg is also a self-edge: swapping caller and callee
   * when they are the same entity is a no-op, so both legs carry this flag and
   * both need the same treatment.
   */
  isSelfCall: boolean;
}

/**
 * An interaction that could NOT become edges, and why.
 *
 * EDGE CASE (unresolved participant): `caller_entity_id` or `callee_entity_id`
 * is null — the processor derived the interaction but could not attribute one of
 * its two ends to an **Entity** (an inferred tool call with no identifying span,
 * a call out to a service the trace never names). A graph edge needs two
 * endpoints, so such an interaction cannot be drawn.
 *
 * REPORTED ONCE PER INTERACTION, NOT PER LEG. The unresolved participant is a
 * property of the interaction's identity row, shared by both its legs, so a null
 * callee kills the request edge and the response edge for the same single reason.
 * Emitting two entries would inflate the notice's count and invite the reader to
 * think two distinct things went wrong — and worse, an in-flight interaction with
 * one leg would then report "1" while a completed one reported "2" for the
 * identical defect, making the number a function of response timing rather than
 * of the data problem. So the check is made once, before the legs are walked.
 *
 * It is emphatically NOT dropped silently: every one is returned here and the
 * view reports the count, because "this trace has 4 interactions but the graph
 * shows 2 arrows" is exactly the kind of silent under-reporting a governance
 * reader must never have to discover for themselves.
 */
export interface DroppedInteraction {
  id: string;
  label: string;
  /** Which end(s) were unresolved. */
  missing: 'caller' | 'callee' | 'both';
  /** The endpoint that WAS resolved, if any — useful context in the notice. */
  resolvedEntityId: string | null;
  /** How many legs this interaction had, so the notice can say what was lost. */
  legCount: number;
}

/** The whole derived graph: what the view renders, plus what it must disclose. */
export interface GraphSpec {
  nodes: GraphNodeSpec[];
  /** One per leg, ordered by the trace-wide leg `seq`. */
  edges: GraphEdgeSpec[];
  /** Interactions with an unresolved participant; never silently discarded. */
  dropped: DroppedInteraction[];
  /**
   * Edge ids grouped by unordered endpoint pair, for pairs carrying more edges
   * than one interaction's own request/response pair explains.
   *
   * EDGE CASE (parallel edges) under the leg model. A request+response pair
   * between A and B is now, by construction, two edges in the same visual channel
   * — that is the EXPECTED shape of every completed interaction, so counting it
   * as "parallel" would fire this notice on essentially every trace and train the
   * reader to ignore it. Noise that is always on carries no information.
   *
   * So the grouping counts DISTINCT INTERACTIONS per unordered pair, not edges: a
   * pair is parallel when two or more *interactions* run between the same two
   * entities (an agent calling the same tool twice), which is the genuinely
   * notable thing the notice was always about. Each interaction is still its own
   * governance fact and each leg still its own edge — nothing is collapsed into
   * one arrow with a count, which would lose the per-leg identity the rest of the
   * UI keys on. `edgeIds` lists every edge in the channel (all legs of all those
   * interactions) so the renderer can fan them out instead of drawing them
   * exactly on top of each other.
   */
  parallelGroups: Array<{ key: string; edgeIds: string[] }>;
}

/**
 * The unordered endpoint-pair key for parallel-edge grouping. Unordered (sorted)
 * because an A→B and a B→A edge occupy the same visual channel between the two
 * nodes and would overlap just as badly as two A→B edges.
 */
function pairKey(a: string, b: string): string {
  return a <= b ? `${a} ${b}` : `${b} ${a}`;
}

/**
 * Assign each node its **column** — its call depth along the REQUEST direction.
 *
 * The layering half of the layout (the row half is the encounter chronology, done
 * inline in `deriveGraph`). Returns a column per node id, dense from 0 wherever
 * the call graph allows.
 *
 * THE ALGORITHM is a longest-path relaxation with a hard ceiling on the column,
 * not a recursive walk:
 *
 *   - every node starts at column 0;
 *   - repeatedly, for each request edge `u → v` with `u !== v`, if
 *     `col[v] <= col[u]` and `col[u] + 1 <= maxColumn` then
 *     `col[v] = col[u] + 1`;
 *   - stop when a pass changes nothing, or after `n` passes, whichever is first;
 *   - `maxColumn` is `n - 1`, the most columns `n` nodes can occupy.
 *
 * WHY A CEILING AND NOT JUST A FIXED POINT. A request cycle is real data: two
 * agents can request each other (A asks B for a plan, B asks A for context), and a
 * self-request is already a first-class case here (`isSelfCall`). On a cycle a
 * plain "run until nothing changes" relaxation never settles — each trip round
 * pushes every node on it one further right, forever — and a recursive
 * longest-path walk recurses forever.
 *
 * The ceiling is what makes termination AND a sane picture both properties of the
 * CODE rather than of the data. `n - 1` is not an arbitrary safety limit: the
 * longest simple path in a graph of `n` nodes has exactly `n - 1` edges, so an
 * acyclic call graph is fully relaxed strictly below the ceiling and it is never
 * the thing that stops it. On a cyclic one it binds, and it binds at the tightest
 * value that cannot distort an acyclic answer — so a 2-cycle yields two adjacent
 * columns rather than the runaway spread a pass-count-only cap would give (`n`
 * passes over `m` edges can advance a cycle node `n * m` times, which is why
 * capping the passes alone is not enough).
 *
 * The result is a deterministic, finite assignment in which the cycle's edges have
 * been pushed apart as far as `n - 1` columns allow, leaving ONE backwards edge in
 * the cycle — the best any layered layout can do, since a cycle cannot be drawn
 * strictly left-to-right. That is a property of cycles, not of this code.
 *
 * The pass cap stays as well, as the belt to the ceiling's braces: with the
 * ceiling in place a pass can still make a change without making progress, so
 * "stop after `n` passes" is what guarantees the loop exits rather than relying on
 * the ceiling to starve it.
 *
 * SELF-REQUESTS ARE SKIPPED (`u !== v`). A self-call would otherwise demand a
 * node be strictly to the right of itself, which is unsatisfiable, and under the
 * cap it would simply march that one node `n` columns right for no reason. The
 * self-edge is drawn as a loop on the node instead (see `isSelfCall`), so it
 * needs no horizontal room.
 *
 * DETERMINISM. `requestEdges` arrives in `seq` order and is iterated in that
 * order, and `max` is order-independent anyway, so the assignment is a pure
 * function of the input with no Map/Set iteration-order dependence beyond
 * insertion.
 *
 * NODES NO REQUEST LEG TOUCHES stay at column 0 — an isolated entity, or one only
 * a dropped interaction names, or one that only ever appears on a response. The
 * caller decides where such nodes go; see `deriveGraph`'s isolated-column note.
 */
function assignColumns(
  nodeIds: readonly string[],
  requestEdges: ReadonlyArray<{ source: string; target: string }>,
): Map<string, number> {
  const column = new Map<string, number>();
  for (const id of nodeIds) column.set(id, 0);

  // The most columns `n` nodes can occupy — the length of the longest simple path.
  // An acyclic graph never reaches it; a cycle is stopped BY it. See the note above.
  const maxColumn = Math.max(0, nodeIds.length - 1);

  // `n` passes, not "until stable": with the ceiling in place a pass can change
  // something without making progress, so the loop needs its own exit.
  for (let pass = 0; pass < nodeIds.length; pass += 1) {
    let changed = false;
    for (const e of requestEdges) {
      // A self-request cannot be to the right of itself; drawn as a loop instead.
      if (e.source === e.target) continue;
      const from = column.get(e.source);
      const to = column.get(e.target);
      // An edge naming an id outside `nodeIds` cannot happen (deriveGraph drops
      // dangling endpoints before this runs), but reading defensively keeps the
      // helper honest on its own signature rather than on a caller's invariant.
      if (from == null || to == null) continue;
      if (to <= from && from + 1 <= maxColumn) {
        column.set(e.target, from + 1);
        changed = true;
      }
    }
    if (!changed) break;
  }

  return column;
}

/**
 * Derive the graph from a trace's entities and interactions.
 *
 * Every entity becomes a node (including isolated ones), tagged with its
 * first-encounter slot (`encounterIndex`) AND with the layered layout's
 * `column` (call depth along request legs) and `row` (chronological position
 * within that column) — so the view can place a node with arithmetic and no
 * layout algorithm of its own. Every LEG of every
 * interaction with BOTH endpoints resolved becomes an edge, in `seq` order; the
 * interactions whose endpoints are unresolved are reported in `dropped`. An
 * endpoint naming an entity that is not in `entities` is also dropped — a
 * dangling source/target would make the topology model invalid, and an entity the
 * interactions reference but the entities read does not return is itself an
 * inconsistency worth surfacing rather than crashing on.
 */
export function deriveGraph(
  entities: readonly Entity[],
  interactions: readonly Interaction[],
): GraphSpec {
  const byId = new Map<string, Entity>();
  for (const e of entities) byId.set(e.id, e);

  // Which entity ids any interaction actually touches — the isolated-node test.
  // Built from ALL interactions, including the dropped ones and the leg-less
  // ones: an interaction with one resolved end still proves that end
  // participated, so the entity is not isolated even though no edge could be
  // drawn for it. Note this reads the identity row, not the legs — an interaction
  // whose response has not arrived still names both its participants.
  const touched = new Set<string>();
  for (const ix of interactions) {
    if (ix.caller_entity_id) touched.add(ix.caller_entity_id);
    if (ix.callee_entity_id) touched.add(ix.callee_entity_id);
  }

  // The drawable/undrawable split is a property of the INTERACTION (its two
  // participant ids), so it is decided once per interaction here — before any leg
  // is looked at. That is what keeps `dropped` one-entry-per-interaction and
  // independent of how many legs happen to have arrived.
  const dropped: DroppedInteraction[] = [];
  const drawable = new Set<string>();
  for (const ix of interactions) {
    const caller = ix.caller_entity_id;
    const callee = ix.callee_entity_id;
    // Unresolved (null) OR dangling (names an entity the entities read does not
    // carry) counts as missing: either way there is no node to attach to.
    const hasCaller = caller != null && byId.has(caller);
    const hasCallee = callee != null && byId.has(callee);

    if (hasCaller && hasCallee) {
      drawable.add(ix.id);
      continue;
    }
    dropped.push({
      id: ix.id,
      label: ix.summary || ix.id,
      missing: !hasCaller && !hasCallee ? 'both' : !hasCaller ? 'caller' : 'callee',
      resolvedEntityId: hasCaller ? caller : hasCallee ? callee : null,
      legCount: (ix.legs ?? []).length,
    });
  }

  // THE reuse point: the Flat view's own row derivation, not a second walk of
  // `interactions`. It already flattens interactions to legs and sorts them by
  // the trace-wide `seq`, which is exactly the edge set and exactly the edge
  // order this view wants — so the graph's arrows and the Flat table's rows are
  // the same list, guaranteed, rather than by coincidence.
  //
  // An interaction with only a request leg contributes exactly one row here and
  // therefore exactly one edge: there is no response leg to invent, so no phantom
  // return arrow is drawn for a call still in flight.
  const edges: GraphEdgeSpec[] = [];
  for (const { ix, leg } of flatLegRows(interactions)) {
    if (!drawable.has(ix.id)) continue; // already reported once, in `dropped`
    // The per-leg swap, shared with FlatLegsTable (flow.legDirection): a request
    // flows caller → callee, a response flows back callee → caller.
    const { from, to } = legDirection(ix, leg);
    // `drawable` already proved both ids are non-null resolved entities; this
    // narrows the types without a non-null assertion.
    if (from == null || to == null) continue;

    edges.push({
      id: `${ix.id}:${leg.leg_type}`,
      interactionId: ix.id,
      legType: leg.leg_type,
      source: from,
      target: to,
      label: String(leg.seq),
      seq: leg.seq,
      title: ix.summary || ix.id,
      // This leg's own error, tri-state: only an explicit `true` is a failure.
      isError: leg.error === true,
      // Same entity at both ends. Read off the interaction rather than
      // `from === to` for clarity; the swap cannot change the answer.
      isSelfCall: ix.caller_entity_id === ix.callee_entity_id,
    });
  }

  // FIRST-ENCOUNTER ORDER (see GraphNodeSpec.encounterIndex). Built AFTER the
  // edges, and from them, rather than from `entities` or `interactions`: the
  // edges are the seq-ordered list of what the graph actually draws, so a slot is
  // only ever handed to an entity the reader can see arrive.
  //
  // Source before target within one edge: the leg travelled that way, so its
  // origin is the earlier sighting. A self-call adds its single entity once (the
  // Set makes the second add a no-op) rather than burning two slots on it.
  const encounterOrder: string[] = [];
  const seen = new Set<string>();
  for (const e of edges) {
    for (const id of [e.source, e.target]) {
      if (seen.has(id)) continue;
      seen.add(id);
      encounterOrder.push(id);
    }
  }
  // Then everything the walk never reached, in `entities` order — the isolated
  // entities and any named only by a dropped interaction. Appending them here
  // (rather than interleaving) is what makes the index dense over `nodes` while
  // keeping the encountered prefix a faithful chronology.
  for (const e of entities) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    encounterOrder.push(e.id);
  }
  const encounterIndexById = new Map(encounterOrder.map((id, i) => [id, i]));

  // THE LAYERING. Column = call depth along REQUEST legs only (see
  // GraphNodeSpec.column and assignColumns for why responses are excluded and why
  // the relaxation is capped rather than run to a fixed point).
  const columnById = assignColumns(
    entities.map((e) => e.id),
    edges.filter((e) => e.legType === 'request'),
  );

  // ISOLATED / UNREACHED ENTITIES GO IN THEIR OWN TRAILING COLUMN, one past the
  // deepest real one, rather than sharing column 0 with the trace's entry points.
  //
  // Column 0 is a CLAIM — "nothing calls this, the flow starts here" — and an
  // entity no leg ever touches has not earned it. Parking them there would put a
  // node the trace never used at the head of the reading order, which is exactly
  // backwards, and would mix them into the entry points' column where a reader
  // could not tell which is which. A trailing column keeps them visible (an
  // isolated entity is a governance fact, and the view already discloses them in
  // an alert) while placing them off the end of the readable chain — the same
  // "present but unconnected" story the dashed outline and the trailing
  // `encounterIndex` slots already tell.
  //
  // Decided from EVERY drawn edge, not just the request legs — and the difference is a
  // real defect, not a refinement. The columns are assigned from request edges alone (a
  // response must not push its target rightwards), and this set used to reuse that same
  // filter on the reasoning that "an entity that only ever appears as a response's source
  // is the callee of a request, so it is reached anyway". That ASSERTS an invariant the
  // code does not enforce: it holds only if a response leg never arrives without its
  // request leg, and nothing here guarantees that — `flatLegRows` imposes no such rule and
  // `legOfType` treats a leg list as arbitrary. Feed `deriveGraph` an interaction carrying
  // only a response leg and both of its participants land in the trailing "unconnected"
  // column, styled as present-but-unconnected, with a drawn edge running between them.
  //
  // The honest test is whether the node has an edge at all: if an arrow touches it, it is
  // not isolated, whatever kind of leg drew that arrow. Columns keep the narrower filter
  // for their own separate reason, so the two are now deliberately different sets rather
  // than one set doing two jobs.
  const hasAnyEdge = new Set<string>();
  for (const e of edges) {
    hasAnyEdge.add(e.source);
    hasAnyEdge.add(e.target);
  }
  const unreached = entities.filter((e) => !hasAnyEdge.has(e.id));
  // `-1` when nothing is reached at all (a trace with no drawable request leg), so
  // the trailing column is 0 and the unreached entities are the whole picture
  // rather than being pushed off into empty space beside nothing.
  const deepestReached = Math.max(
    -1,
    ...entities.filter((e) => hasAnyEdge.has(e.id)).map((e) => columnById.get(e.id) ?? 0),
  );
  const trailingColumn = deepestReached + 1;
  for (const e of unreached) columnById.set(e.id, trailingColumn);

  // NORMALISE so the leftmost occupied column is 0. Done HERE, after the trailing column
  // is assigned, and not inside `assignColumns` — the unreached entities are placed after
  // that helper returns, so normalising earlier would fix a minimum that is not yet final.
  //
  // Without this, a cycle drifts rightward with the count of nodes that have nothing to do
  // with it. `assignColumns`' ceiling is `n - 1` over ALL nodes and a cycle relaxes until
  // it hits that ceiling, so adding entities which participate in no request edge *moves
  // the cycle*: `A⇄B` alone occupies columns 0 and 1, while `A⇄B` plus eight isolated
  // entities occupies 8 and 9 — leaving columns 0-7 empty and the renderer drawing a
  // growing blank left gutter.
  //
  // That also silently vacated `GraphNodeSpec.column`'s documented contract, which says
  // `0` means "an entity nothing ever calls — the trace's entry point". On a wholly cyclic
  // call graph no node held column 0 at all, so the field meant nothing.
  //
  // Shifting is safe because only the RELATIVE column is load-bearing: the renderer
  // consumes `column` as a left-to-right ordinal (multiplying it by a px step), and every
  // edge's routing depends on the DIFFERENCE between two columns, which a uniform shift
  // preserves exactly. The pre-existing tests pass either way — they assert
  // `column <= n - 1` and that a 2-cycle occupies two distinct columns, both true before
  // and after — which is why this went unnoticed; `min === 0` is what actually pins it.
  let minColumn = Infinity;
  for (const c of columnById.values()) if (c < minColumn) minColumn = c;
  if (minColumn > 0 && minColumn !== Infinity) {
    for (const [id, c] of columnById) columnById.set(id, c - minColumn);
  }

  // THE ROWS. Within each column, nodes stack in `encounterIndex` order — the
  // trace's chronology — so reading down a column reads forwards in time. THIS IS
  // THE USER'S EXAMPLE AS A GUARANTEE: "A calls B, then A calls C" puts B and C
  // both at depth 1, and B was encountered first, so B is row 0 and C is row 1 —
  // B is ABOVE C. See GraphNodeSpec.row.
  //
  // Sorted by `encounterIndex` and not by `seq`: a node has no single seq (it
  // participates in many legs) whereas its first encounter is exactly one number,
  // and it is already the trace-order fact this module derives. The isolated
  // group's `encounterIndex` falls back to `entities` order, which is the API's
  // order and stable for a trace, so their rows are deterministic too.
  const rowById = new Map<string, number>();
  const byColumn = new Map<number, string[]>();
  for (const e of entities) {
    const col = columnById.get(e.id) ?? 0;
    const bucket = byColumn.get(col);
    if (bucket) bucket.push(e.id);
    else byColumn.set(col, [e.id]);
  }
  for (const ids of byColumn.values()) {
    ids
      .slice()
      .sort(
        (a, b) => (encounterIndexById.get(a) ?? 0) - (encounterIndexById.get(b) ?? 0),
      )
      .forEach((id, row) => rowById.set(id, row));
  }

  // `nodes` stays in `entities` order — several callers and tests read it
  // positionally, and the ordering is carried as a FIELD instead so the two
  // notions cannot be confused. Reordering the array would also silently change
  // z-order in the rendered SVG.
  const nodes: GraphNodeSpec[] = entities.map((e) => ({
    id: e.id,
    // An entity with a blank display_name still needs a readable node; the id is
    // the only other thing guaranteed present.
    label: e.display_name || e.id,
    kind: e.kind,
    naturalKey: e.natural_key,
    isIsolated: !touched.has(e.id),
    // Every entity id is in the map: the second loop above covers whatever the
    // edge walk did not. `?? 0` is unreachable, and is only here to avoid a
    // non-null assertion the lint config rejects.
    encounterIndex: encounterIndexById.get(e.id) ?? 0,
    // Same reasoning for these two `??`s: every entity id was seeded into both
    // maps above, so neither fallback is reachable.
    column: columnById.get(e.id) ?? 0,
    row: rowById.get(e.id) ?? 0,
  }));

  // Group by unordered pair. The grouping decision counts distinct INTERACTIONS
  // in the channel, not edges, so a lone interaction's request/response pair —
  // the normal shape — is not reported as parallel (see parallelGroups' note).
  const groups = new Map<string, { edgeIds: string[]; interactionIds: Set<string> }>();
  for (const e of edges) {
    const k = pairKey(e.source, e.target);
    let g = groups.get(k);
    if (!g) {
      g = { edgeIds: [], interactionIds: new Set() };
      groups.set(k, g);
    }
    g.edgeIds.push(e.id);
    g.interactionIds.add(e.interactionId);
  }
  const parallelGroups = [...groups.entries()]
    .filter(([, g]) => g.interactionIds.size > 1)
    .map(([key, g]) => ({ key, edgeIds: g.edgeIds }));

  return { nodes, edges, dropped, parallelGroups };
}
