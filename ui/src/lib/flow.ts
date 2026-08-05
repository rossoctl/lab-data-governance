/**
 * Pure data-layer helpers for the interaction-flow view.
 *
 * Ported from `data_governance/api/ui/execution_flow_logic.js`:
 * - `toolSubtype`: deployed vs in-framework tool, from the natural-key SHAPE.
 * - `computeInteractionDepths`: indentation depth by walking parent links,
 *   order-independent and cycle/missing-parent guarded.
 * - `durationMs`: the (ended - started) ms formula shared with the span panel.
 */

/**
 * How the flow view's Interactions section presents this trace's interactions:
 * the default parent/child `tree` (depth-indented interactions), `flat` (one row
 * per request/response leg, ordered by the trace-wide leg `seq`), `diagram` (a
 * UML-style sequence diagram of that same flat leg sequence — lifelines across
 * the top, one arrow per leg down the page), `graph` (the Execution Flow — the
 * same interactions drawn as a directed who-called-whom graph), or `lineage` (that
 * SAME graph with the selected entity's **Data lineage** sources highlighted).
 * Mirrored to/from the URL as `?legs=flat` / `?legs=diagram` / `?legs=graph` /
 * `?legs=lineage`; `tree` is the default and writes no param, so canonical URLs
 * stay clean.
 *
 * `graph` joined this set (rather than staying the top-level `/graph` view
 * segment it was) because all of them are presentations of the SAME two reads the
 * flow view already holds: the tables answer "what happened, in order", the graph
 * answers "who talked to whom". A top-level tab claimed it was a peer of the span
 * tree — a different dataset — which it never was.
 *
 * `diagram` sits between `flat` and `graph` for the same reason, and in that
 * ORDER deliberately: it is the flat leg list read down the page (so it belongs
 * next to `flat`, whose row order it reproduces exactly), with the who-called-whom
 * axis of the graph laid out horizontally. It is the two neighbours' shared
 * middle, not a fourth unrelated dataset.
 *
 * `lineage` sits LAST, immediately after `graph`, and the adjacency is the point:
 * it draws the identical node/edge set (one `deriveGraph`, one component — see
 * `lib/lineageReachability` and `ExecutionFlowGraph`) and adds exactly one thing, a
 * highlight of where the selected entity's data came from. Ordering it after
 * `graph` says "same picture, one more question asked of it"; putting it anywhere
 * earlier would separate it from the view it is a reading of. It also reads one
 * MORE resource than its four neighbours (the trace's data-lineage, already held
 * by the flow view), which is a second reason it is not one of them.
 *
 * Lives here, in the flow view's pure data module, rather than in `FlowTables`:
 * the page owns the URL and the component owns the tab bar, so both need the type and
 * the coercion, and neither is a natural owner of it.
 *
 * NOTE ON THE ORDERING ARGUMENT ABOVE, which is about how these five are PRESENTED and
 * is now only half this type's business: `diagram`, `graph` and `lineage` were promoted
 * from `?legs` sub-tabs to top-level views addressed by PATH SEGMENT
 * (`TraceDetailPage`'s ViewKey), so the tab order the paragraphs above justify is
 * realised by that page's tab bar, not by the `?legs` bar — which is now Tree|Flat only.
 * The five values themselves are unchanged: this type still names what `FlowTables` can
 * render, which is why the promotion was a navigation change and not a rewrite. Only
 * `tree` and `flat` are valid `?legs` values — see {@link parseLegViewKey}.
 */
export type LegViewKey = 'tree' | 'flat' | 'diagram' | 'graph' | 'lineage';

/**
 * Coerce an arbitrary `?legs` value to a `LegViewKey`. Anything unrecognised —
 * and absent — reads as the default `tree` rather than throwing, matching
 * `parseWindowKey`'s treatment of `?window`. Stated once so the page's URL read
 * and the tab bar's `onSelect` cannot drift about what a valid value is.
 *
 * ONLY `tree` AND `flat` ARE `?legs` VALUES NOW. `diagram`, `graph` and `lineage` are
 * still members of {@link LegViewKey} — that type names the five PRESENTATIONS
 * `FlowTables` can render, which has not changed — but they are addressed by PATH
 * SEGMENT rather than by this param since they were promoted to top-level views
 * (`TraceDetailPage`'s ViewKey).
 *
 * So a leftover `?legs=graph` coerces to `tree` HERE, and that is deliberately not how
 * such a URL is handled: `TraceDetailPage` intercepts the three legacy values before
 * this function is consulted and redirects them to their new segment
 * (LEGACY_LEGS_TO_VIEW), so an old bookmark lands on the view it named instead of
 * falling back to the tables. This coercion is the last line of defence for a value
 * that is genuinely junk, not the migration path.
 */
export function parseLegViewKey(raw: string | null | undefined): LegViewKey {
  return raw === 'flat' ? raw : 'tree';
}

/**
 * Coerce the Lineage tab's `?src` param — the **Entity natural key** of the single
 * data source being traced — to `string | null`.
 *
 * WHY THIS EXISTS BESIDE {@link parseLegViewKey} RATHER THAN INSIDE THE PAGE. Same
 * reason: the page reads the param and a component's control writes it, so the two
 * need one shared notion of "a valid value" or they drift. It is a sibling of that
 * function on purpose, so the pattern for adding a flow-view URL param is one thing
 * and not two.
 *
 * WHY IT IS NOT AN ENUM COERCION LIKE `?legs` IS. A natural key is open-ended data
 * (`tool:agent:(travel_advisor,travel-advisor):search_destinations`), so there is no
 * closed set to validate against HERE — the only authority on which sources exist is
 * the trace's own `data-lineage-summary`, which this module cannot read. So this
 * function does exactly the syntactic half (absent or blank → `null`, so `?src=`
 * cannot become a request for a source named empty string), and the SEMANTIC half —
 * "is this a source THIS trace has?" — belongs to
 * `lineageReachability.resolveSourceChoice`, which has the roll-up in hand and
 * reports a mismatch as its own `'stale'` state rather than silently blanking it.
 * That split is what makes a bad `?src` behave like a bad `?legs`: coerced, never
 * thrown, and never sent to the server.
 */
export function parseLineageSource(raw: string | null | undefined): string | null {
  const trimmed = raw?.trim();
  return trimmed ? trimmed : null;
}

/** A derived entity (ADR-0013): cross-trace-stable, no trace_id column. */
export interface Entity {
  id: string;
  kind: string;
  natural_key: string;
  display_name: string;
  detected_from: string;
}

/** One temporal half of an interaction (ADR-0025): a request or a response. */
export interface InteractionLeg {
  leg_type: 'request' | 'response';
  occurred_at: string | null;
  payload_hash: string | null;
  error: boolean | null;
  seq: number;
}

/**
 * A derived interaction (ADR-0025) for one trace: a parent identity row plus
 * one or two request/response `legs`. The leg-dependent fields (timing,
 * payload, error) live on the legs; the accessors below project them back for
 * display. `duration_seconds` is computed by the API (null = response in
 * flight); `any_error` aggregates the legs.
 */
export interface Interaction {
  id: string;
  caller_entity_id: string | null;
  callee_entity_id: string | null;
  summary: string | null;
  parent_interaction_id: string | null;
  legs: InteractionLeg[];
  duration_seconds: number | null;
  any_error: boolean | null;
  span_count: number;
  anchor_count: number;
}

/** The request (or response) leg of an interaction, if present. */
export function legOfType(
  ix: Pick<Interaction, 'legs'>,
  legType: 'request' | 'response',
): InteractionLeg | null {
  return (ix.legs ?? []).find((l) => l.leg_type === legType) ?? null;
}

/**
 * The interaction's start = its request leg's `occurred_at` (ADR-0025). The
 * flow table orders and displays on this, exactly as it used to read
 * `started_at` off the interaction row.
 */
export function requestOccurredAt(ix: Pick<Interaction, 'legs'>): string | null {
  return legOfType(ix, 'request')?.occurred_at ?? null;
}

/** The interaction's end = its response leg's `occurred_at`, if any. */
export function responseOccurredAt(ix: Pick<Interaction, 'legs'>): string | null {
  return legOfType(ix, 'response')?.occurred_at ?? null;
}

/**
 * The canonical **Interaction leg** key, `(interaction_id, leg_type)` — the
 * grain at which a **Data lineage** fact is unique (ADR-0028 D5). Single-sourced
 * here so the hook that builds the lookup map and the view that reads it can't
 * drift on the separator. Deliberately NOT keyed on `payload_hash`: payloads are
 * content-addressed and deduped, so identical bytes at two positions would
 * collide two completely different lineages into one entry.
 */
export function legLineageKey(
  interactionId: string,
  legType: 'request' | 'response',
): string {
  return `${interactionId}:${legType}`;
}

/** One flat-view row: a single leg, plus the interaction it belongs to. */
export interface FlatRow {
  ix: Interaction;
  leg: InteractionLeg;
}

/**
 * The flat view's rows: one entry per leg across all interactions, ordered by
 * the trace-wide leg `seq`, ignoring the parent/child tree. Each carries its
 * parent interaction so a click still opens that interaction's detail panel
 * (legs have no selection of their own).
 */
export function flatLegRows(interactions: readonly Interaction[]): FlatRow[] {
  return interactions
    .flatMap((ix) => (ix.legs ?? []).map((leg) => ({ ix, leg })))
    .sort((a, b) => a.leg.seq - b.leg.seq);
}

/**
 * Which way a single **leg** flows between its interaction's two participants
 * (ADR-0025).
 *
 * An interaction names a fixed `caller_entity_id` → `callee_entity_id` pair, but
 * that is the direction of the *call*, not of each leg: the request travels
 * caller → callee and the response travels back callee → caller. So a completed
 * interaction is TWO opposite-direction movements, not one.
 *
 * Stated once, here, because two views need the identical rule and must never
 * disagree: the Flat table's Caller/Callee columns and the Execution Flow
 * graph's edge direction. When this was written twice, a response row could read
 * `A → B` in the table while the graph drew `B → A` for the same leg — and
 * neither would be obviously wrong on its own.
 *
 * Returns the interaction's ids un-swapped for a request leg and swapped for a
 * response leg. Null ids pass straight through (an unresolved participant stays
 * unresolved whichever end of the leg it is on); callers decide what to do with
 * them — the table renders a blank cell, the graph drops the edge and reports it.
 */
export function legDirection(
  ix: Pick<Interaction, 'caller_entity_id' | 'callee_entity_id'>,
  leg: Pick<InteractionLeg, 'leg_type'>,
): { from: string | null; to: string | null } {
  const isResponse = leg.leg_type === 'response';
  return {
    from: isResponse ? ix.callee_entity_id : ix.caller_entity_id,
    to: isResponse ? ix.caller_entity_id : ix.callee_entity_id,
  };
}

/**
 * Request↔response pairing for the flat view's connector column, as one entry
 * per row of `flatLegRows`.
 *
 * A request leg and its response leg share the same `ix.id` (that is the pairing
 * key), but they sort by `seq` so they are frequently NOT adjacent — other
 * interactions' legs interleave between them. We map `ix.id` → the row indices of
 * its request and response, then derive each interaction's [top, bottom] index
 * span. A row then knows, for every interaction whose span covers it, whether it
 * is that span's top edge (request → half-line down + ▾), its bottom edge
 * (response → half-line up + ▴), or an in-between pass-through (full vertical
 * line). Single-leg interactions (response in flight) have only one index, so
 * their span is a single row with no partner and thus no line is drawn.
 */
export function flatConnectorRoles(
  rows: readonly FlatRow[],
): Array<Array<{ id: string; role: 'top' | 'bottom' | 'through' }>> {
  const spans = new Map<string, { top: number; bottom: number }>();
  rows.forEach(({ ix }, i) => {
    const s = spans.get(ix.id);
    if (!s) spans.set(ix.id, { top: i, bottom: i });
    else s.bottom = i; // later index (legs already sorted by seq)
  });
  // Per row, the drawing role for each interaction whose span covers it.
  return rows.map((_row, i) =>
    [...spans.entries()]
      .filter(([, s]) => s.top !== s.bottom && i >= s.top && i <= s.bottom)
      .map(([id, s]) => ({
        id,
        role: i === s.top ? ('top' as const) : i === s.bottom ? ('bottom' as const) : ('through' as const),
      })),
  );
}

export type ToolSubtype = 'in-framework' | 'deployed';

/**
 * Distinguish the two tool flavours purely by natural-key shape (no schema
 * field). A tool deployed as its own MCP service is exactly
 * `tool:(<project>,<service>)` — a single parenthesised tuple with nothing
 * after the closing paren. Everything else under `tool:` is in-framework
 * (hosted by an agent), including `tool:(unknown):<name>` (trailing `:<name>`).
 * Returns null for non-tool entities or non-`tool:` keys.
 */
export function toolSubtype(entity: Pick<Entity, 'kind' | 'natural_key'> | null): ToolSubtype | null {
  if (!entity || entity.kind !== 'tool') return null;
  const nk = entity.natural_key || '';
  if (!nk.startsWith('tool:')) return null;
  if (/^tool:\([^)]*\)$/.test(nk)) return 'deployed';
  return 'in-framework';
}

/**
 * Compute each interaction's tree depth by following `parent_interaction_id`
 * up through the full set, memoised and guarded against cycles / missing
 * parents. Rows arrive ordered by `started_at`, which is NOT topological (a
 * parent can sort after its child), so a single forward pass would misplace
 * such a child at depth 0.
 */
export function computeInteractionDepths(
  interactions: readonly Interaction[],
): Map<string, number> {
  const ixById = new Map<string, Interaction>();
  for (const ix of interactions) ixById.set(ix.id, ix);

  const depthById = new Map<string, number>();
  const depthOf = (id: string): number => {
    const memo = depthById.get(id);
    if (memo !== undefined) return memo;
    depthById.set(id, 0); // cycle/self guard: break re-entry at this node
    const ix = ixById.get(id);
    const pid = ix?.parent_interaction_id;
    const d = pid && ixById.has(pid) ? depthOf(pid) + 1 : 0;
    depthById.set(id, d);
    return d;
  };
  for (const ix of interactions) depthOf(ix.id);
  return depthById;
}

/**
 * Which glyph a span-evidence `role` renders with, in the flow detail panel's
 * Spans table. The two role enums live on different relations but share a
 * "weight" reading, encoded by the icon:
 * - `key`   — the creating/first-evidence span. `anchor` (created the
 *             interaction) and `discovered_via` (first revealed the entity)
 *             both get it: exactly-one, highest weight, the defining span.
 * - `info`  — `info`: a non-anchor span that carried payload/error content.
 * - `dot`   — `identified_via`: a later span that re-confirmed an already-known
 *             entity (repeat sighting, lower weight than the key).
 * - `minus` — `connector`: present in the interaction's territory but
 *             contributed nothing classifiable (the faintest, thin-dash mark).
 * - `none`  — any unrecognised role (no glyph, raw label).
 *
 * The DB enums are unchanged — this is a display-only map (see CONTEXT.md
 * **Entity-span role** / **Interaction-span role**).
 */
export type RoleIconKind = 'key' | 'info' | 'dot' | 'minus' | 'none';

interface RoleMeta {
  /** Human-readable role text, for the tooltip and accessible label. */
  label: string;
  icon: RoleIconKind;
}

const ROLE_META: Record<string, RoleMeta> = {
  anchor: { label: 'anchor', icon: 'key' },
  discovered_via: { label: 'discovered via', icon: 'key' },
  info: { label: 'info', icon: 'info' },
  identified_via: { label: 'identified via', icon: 'dot' },
  connector: { label: 'connector', icon: 'minus' },
};

export function roleMeta(role: string): RoleMeta {
  return ROLE_META[role] ?? { label: role, icon: 'none' };
}

/**
 * (ended - started) in milliseconds to 3 decimals, or null when either end is
 * absent. Same formula/format the span detail panel uses.
 */
export function durationMs(startedAt: string | null, endedAt: string | null): string | null {
  if (startedAt == null || endedAt == null) return null;
  const ms = new Date(endedAt).getTime() - new Date(startedAt).getTime();
  return ms.toFixed(3);
}
