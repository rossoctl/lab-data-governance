/**
 * Pure lifeline/message derivation for the Interaction diagram (sequence-diagram)
 * view.
 *
 * Sibling to `./flow.ts` and `./graph.ts`: `flow.ts` holds the flat-leg / depth
 * derivations the tables need, `graph.ts` the entity→node and leg→edge derivations
 * the topology graph needs, and this one the entity→LIFELINE and leg→MESSAGE
 * derivations a UML sequence diagram needs. All three are deliberately
 * render-free, for the reason graph.ts states and this view feels even more
 * sharply: jsdom implements no SVG layout, so the SHAPE of the diagram can only be
 * proven as pure logic (see sequenceDiagram.test.ts). The component below it
 * places rectangles and paths and nothing else.
 *
 * ONE MESSAGE PER **LEG**, NOT PER INTERACTION — the same rule the graph follows,
 * and for the same reason. This module consumes `flatLegRows` directly rather than
 * re-walking `interactions`, so the diagram's arrows, the graph's edges and the
 * Flat table's rows are the SAME list in the SAME order by construction rather
 * than by coincidence. A completed interaction therefore contributes TWO messages
 * pointing opposite ways — `A→B` at the request's `seq` and `B→A` at the
 * response's — because that is what actually travelled on the wire (ADR-0025), and
 * a single caller→callee arrow would assert that a response either did not exist
 * or did not travel.
 *
 * WHAT THIS ADDS OVER graph.ts, and why it is not just a re-projection of it: a
 * sequence diagram has a horizontal ORDER (which lifeline gets which column) and a
 * vertical one (which message is on which row). The graph has neither — Dagre
 * decides node placement and the edges carry only a seq label. That column
 * assignment is the whole substance of this module, so it is derived here rather
 * than bolted onto `GraphSpec`.
 */
import { flatLegRows, legDirection, type Entity, type Interaction } from './flow';

/**
 * One lifeline: an **Entity** that at least one drawable message touches, plus the
 * column it occupies.
 */
export interface LifelineSpec {
  /** The entity's id — what a message's from/to indices resolve back through. */
  id: string;
  /** The visible label: the entity's `display_name` (falling back to its id). */
  label: string;
  /** The entity's kind, which drives the head box's colour (see kindColorVar). */
  kind: string;
  /** The entity's natural key, for the head box's hover title. */
  naturalKey: string;
  /**
   * This lifeline's column, 0-based, left to right. Assigned in FIRST-ENCOUNTER
   * order by leg `seq`: the entity that first appears as a message's source or
   * target gets column 0, the next newly-seen entity column 1, and so on.
   *
   * NOT the `entities` read's own order (which is the API's, effectively
   * arbitrary), and not alphabetical. First-encounter order is what makes the
   * diagram read like the call actually flowed — the initiator sits on the left,
   * the things it reached fan out rightwards, and a typical trace's arrows then
   * mostly point right on the way down and left on the way back. Sorting any other
   * way produces a correct diagram that crosses itself for no reason.
   */
  index: number;
}

/**
 * One message: an **Interaction leg**, drawn as a horizontal arrow between two
 * lifelines in that leg's own direction.
 *
 * A request leg is `caller → callee`; a response leg is `callee → caller`. The
 * swap is not decided here — it comes from `flow.legDirection`, shared with the
 * Flat table's Caller/Callee columns and the graph's edge direction, so the three
 * views cannot disagree about which way a leg went.
 */
export interface MessageSpec {
  /**
   * `<interaction id>:<leg_type>` — the canonical **Interaction leg** key, the
   * grain at which a leg is unique (the same grain `flow.legLineageKey` and
   * `graph.GraphEdgeSpec.id` use). The interaction id alone is NOT unique: its two
   * legs are two messages, and a React key collision would drop one row.
   */
  key: string;
  /** The interaction this leg belongs to — what a click SELECTS (see below). */
  interactionId: string;
  /** Which half of the interaction this message is. */
  legType: 'request' | 'response';
  /** The source lifeline's column index. Never out of range (see dropped). */
  fromIndex: number;
  /** The destination lifeline's column index. Never out of range. */
  toIndex: number;
  /** The leg's trace-wide `seq` — the vertical ordering, and the visible tag. */
  seq: number;
  /**
   * The arrow's visible text: this leg's `seq`, as a string. A leg has exactly one
   * `seq`, so the label stays a single number and remains legible on a short
   * horizontal arrow between adjacent lifelines — which is why the interaction's
   * prose `summary` is `title` (hover) rather than the label, exactly as on the
   * graph's edges.
   */
  label: string;
  /** The interaction's `summary` (falling back to its id), for the tooltip. */
  title: string;
  /**
   * True only when THIS LEG's own `error` is exactly `true`.
   *
   * Deliberately the leg's error, not the interaction's `any_error`: a message is
   * one leg, so it must show that leg's outcome. `any_error` ORs both legs, so
   * using it would paint a successful request arrow red merely because the response
   * later failed — reporting a failure at a point in the diagram ABOVE where it
   * happened, which in a view whose whole axis is time is a especially misleading.
   *
   * Tri-state is preserved: `leg.error` is `boolean | null`, and `null` means "not
   * yet aggregated", NOT "succeeded" and NOT "failed". Only an explicit `true`
   * colours the arrow — the same never-render-unknown-as-a-verdict rule
   * `graph.GraphEdgeSpec.isError` and the lineage status follow.
   */
  isError: boolean;
  /**
   * EDGE CASE (self-call): `fromIndex === toIndex`. A genuine self-call (an agent
   * recursing, a tool re-entering itself) is kept as a real message rather than
   * dropped, but a straight horizontal arrow between one lifeline and itself has
   * ZERO length and is invisible — so the renderer must draw a loop out to the side
   * and back. Flagged here so it does not have to re-derive `from === to`.
   *
   * A self-call's RESPONSE leg is also a self-message: swapping caller and callee
   * when they are the same entity is a no-op, so both legs carry the flag and both
   * need the loop treatment.
   */
  isSelfCall: boolean;
}

/**
 * An interaction that could NOT become messages, and why.
 *
 * EDGE CASE (unresolved participant): `caller_entity_id` or `callee_entity_id` is
 * null — the processor derived the interaction but could not attribute one of its
 * ends to an **Entity** (an inferred tool call with no identifying span, a call out
 * to a service the trace never names). An arrow needs a lifeline at each end, so
 * such an interaction cannot be drawn. A dangling id — one naming an entity the
 * `entities` read does not carry — counts the same way: there is no lifeline to
 * attach to, and the inconsistency is worth surfacing rather than crashing on.
 *
 * REPORTED ONCE PER INTERACTION, NOT PER LEG, for the reasons `graph.ts`'s
 * `DroppedInteraction` spells out: the unresolved participant is one defect on the
 * identity row shared by both legs, so counting per leg would both inflate the
 * notice and make the number a function of RESPONSE TIMING (an in-flight
 * interaction reporting 1 where a completed one reports 2 for the identical data
 * problem). The check is therefore made once, before any leg is walked, and the leg
 * count is carried as a field so the arrow arithmetic still adds up for a reader
 * comparing this tab to the Flat one.
 *
 * The shape mirrors `graph.DroppedInteraction` field for field on purpose — two
 * tabs over one dataset must disclose the same defect in the same words. It is
 * restated rather than imported because the two modules are peers and neither
 * should own the other's types; if a third view ever needs it, it moves to
 * `flow.ts` (where the shared leg vocabulary already lives) rather than being
 * imported sideways.
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

/** The whole derived diagram: what the view draws, plus what it must disclose. */
export interface SequenceDiagramSpec {
  /** One per PARTICIPATING entity, in first-encounter order (see `index`). */
  lifelines: LifelineSpec[];
  /** One per drawable leg, ordered by the trace-wide leg `seq`. */
  messages: MessageSpec[];
  /** Interactions with an unresolved participant; never silently discarded. */
  dropped: DroppedInteraction[];
  /**
   * EDGE CASE (isolated entity), and the one place this view deliberately DIVERGES
   * from the Execution Flow graph.
   *
   * The graph draws an isolated entity as a bare node with a dashed outline,
   * because a node is a self-sufficient mark: it says "this entity exists" and
   * needs no edge to mean something. A LIFELINE is not. A lifeline's entire job is
   * to be the thing arrows land on; drawing one that no arrow ever touches adds a
   * column to the layout, pushes every real participant sideways, and asserts
   * — in the grammar of a sequence diagram — that this participant took part in the
   * exchange. It did not.
   *
   * DECISION: isolated entities get NO lifeline, and their COUNT (with ids) is
   * returned here so the component can disclose them in an `Alert`. Omission
   * without disclosure was never on the table — "this trace has 5 entities but the
   * diagram has 3 columns" is exactly the silent under-report the graph's own
   * notices exist to prevent. The rejected alternative (a bare dashed lifeline, for
   * consistency with the graph) was rejected because consistency of MARK is not the
   * goal; consistency of DISCLOSURE is, and both tabs disclose. The Entities table
   * directly above the tabs remains the complete participant list either way.
   *
   * Note "isolated" here means the same thing the graph means: named by NO
   * interaction as caller or callee. An entity whose only interaction was dropped
   * for the OTHER end is NOT isolated — that interaction still proves it
   * participated — but it also gets no lifeline, since no message can reach it.
   * Such an entity is therefore reported in neither list, and the `dropped` notice
   * is what accounts for its absence. That is deliberate: it would otherwise be
   * double-counted under two different explanations.
   */
  isolated: Array<{ id: string; label: string }>;
}

/**
 * Derive the sequence diagram from a trace's entities and interactions.
 *
 * Every LEG of every interaction with both endpoints resolved to a known entity
 * becomes a message, in `seq` order; interactions whose endpoints are unresolved or
 * dangling are reported in `dropped`. Lifelines are the entities those messages
 * actually touch, columned by first encounter; entities no interaction names at all
 * are reported in `isolated` (see the field's note for why they get no column).
 */
export function deriveSequenceDiagram(
  entities: readonly Entity[],
  interactions: readonly Interaction[],
): SequenceDiagramSpec {
  const byId = new Map<string, Entity>();
  for (const e of entities) byId.set(e.id, e);

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
    // carry) counts as missing: either way there is no lifeline to attach to.
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

  // THE reuse point, shared with the graph: the Flat view's own row derivation,
  // not a second walk of `interactions`. It already flattens interactions to legs
  // and sorts them by the trace-wide `seq`, which is exactly the message set and
  // exactly the top-to-bottom order this view wants — so the diagram's rows and
  // the Flat table's rows are the same list, guaranteed.
  //
  // An interaction with only a request leg contributes exactly one row and
  // therefore exactly one arrow: there is no response leg to invent, so no phantom
  // return arrow is drawn for a call still in flight.
  const rows = flatLegRows(interactions).filter(({ ix }) => drawable.has(ix.id));

  // Column assignment, in ONE pass over the already-seq-sorted rows: the first
  // time an entity is seen as a source or a target it claims the next column.
  // Source before target within a leg, so the initiator of the very first request
  // lands in column 0 (a target-first order would put the callee leftmost and make
  // every subsequent arrow point backwards).
  //
  // Note this walks the DRAWABLE rows only. An entity reachable solely through a
  // dropped interaction gets no column, which is the point: no message can land on
  // it, so a column for it would be an empty one.
  const columnOf = new Map<string, number>();
  const claim = (id: string) => {
    if (!columnOf.has(id)) columnOf.set(id, columnOf.size);
  };
  for (const { ix, leg } of rows) {
    const { from, to } = legDirection(ix, leg);
    // `drawable` already proved both ids are non-null resolved entities; these
    // guards narrow the types without a non-null assertion.
    if (from != null) claim(from);
    if (to != null) claim(to);
  }

  const lifelines: LifelineSpec[] = [...columnOf.entries()]
    // Map iteration is insertion-ordered, so this is already first-encounter
    // order; the sort is belt-and-braces documentation of the invariant the
    // consumer relies on (a lifeline at array position i has index i).
    .sort((a, b) => a[1] - b[1])
    .map(([id, index]) => {
      // Non-null: an id only enters `columnOf` after `drawable` proved `byId` has
      // it, so the lookup cannot miss.
      const e = byId.get(id)!;
      return {
        id,
        // An entity with a blank display_name still needs a readable head box; the
        // id is the only other field guaranteed present.
        label: e.display_name || e.id,
        kind: e.kind,
        naturalKey: e.natural_key,
        index,
      };
    });

  const messages: MessageSpec[] = [];
  for (const { ix, leg } of rows) {
    // The per-leg swap, shared with FlatLegsTable and the graph
    // (flow.legDirection): a request flows caller → callee, a response flows back
    // callee → caller.
    const { from, to } = legDirection(ix, leg);
    const fromIndex = from == null ? undefined : columnOf.get(from);
    const toIndex = to == null ? undefined : columnOf.get(to);
    // Unreachable given `drawable` + the claim pass above; kept as a type narrow
    // rather than an assertion so a future change to either cannot produce an
    // arrow pointing at column `undefined`.
    if (fromIndex === undefined || toIndex === undefined) continue;

    messages.push({
      key: `${ix.id}:${leg.leg_type}`,
      interactionId: ix.id,
      legType: leg.leg_type,
      fromIndex,
      toIndex,
      seq: leg.seq,
      label: String(leg.seq),
      title: ix.summary || ix.id,
      // This leg's own error, tri-state: only an explicit `true` is a failure.
      isError: leg.error === true,
      // Same entity at both ends. Read off the interaction rather than
      // `fromIndex === toIndex` for clarity; the swap cannot change the answer.
      isSelfCall: ix.caller_entity_id === ix.callee_entity_id,
    });
  }

  // Which entity ids any interaction actually NAMES — the isolated test, and the
  // same one the graph applies. Built from ALL interactions, including the dropped
  // ones: an interaction with one resolved end still proves that end participated,
  // so such an entity is not "isolated" even though it gets no lifeline (see
  // `isolated`'s note on why it is then reported in neither list).
  const touched = new Set<string>();
  for (const ix of interactions) {
    if (ix.caller_entity_id) touched.add(ix.caller_entity_id);
    if (ix.callee_entity_id) touched.add(ix.callee_entity_id);
  }
  const isolated = entities
    .filter((e) => !touched.has(e.id))
    .map((e) => ({ id: e.id, label: e.display_name || e.id }));

  return { lifelines, messages, dropped, isolated };
}
