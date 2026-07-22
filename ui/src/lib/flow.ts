/**
 * Pure data-layer helpers for the interaction-flow view.
 *
 * Ported from `data_governance/api/ui/execution_flow_logic.js`:
 * - `toolSubtype`: deployed vs in-framework tool, from the natural-key SHAPE.
 * - `computeInteractionDepths`: indentation depth by walking parent links,
 *   order-independent and cycle/missing-parent guarded.
 * - `durationMs`: the (ended - started) ms formula shared with the span panel.
 */

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
