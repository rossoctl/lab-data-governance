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
 * Per-interaction classification kinds, re-derived server-side from the anchor
 * (request) span's attributes by the sanctioned classifier; `null` when the
 * anchor span carries no sidecar lineage facts. Content kinds are null for
 * plain-http interactions (no semantic body kind).
 */
export interface InteractionKinds {
  protocol: string;
  mcp_method: string | null;
  request_content_kind: string | null;
  response_content_kind: string | null;
}

/**
 * Where the interaction's exchange was addressed, re-derived server-side from
 * the anchor span's location facts; `null` when the anchor carries none.
 * `url` is composed only when the scheme fact exists (wire contract v1.5.1) —
 * older spans render host + path without a URL. `internal` marks
 * cluster-local authorities (consumer-side vocabulary).
 */
export interface InteractionDestination {
  url: string | null;
  host: string | null;
  path: string | null;
  internal: boolean | null;
}

/**
 * The interaction's HTTP event, re-derived server-side from the span pair:
 * `method` off the request span, `status_code` / `outcome` off the response
 * span. `null` when the anchor carries no sidecar facts or none of the three
 * facts exists; individual fields are null while the response is in flight.
 */
export interface InteractionHttp {
  method: string | null;
  status_code: number | null;
  outcome: string | null;
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
  trace_id: string;
  caller_entity_id: string | null;
  callee_entity_id: string | null;
  summary: string | null;
  parent_interaction_id: string | null;
  legs: InteractionLeg[];
  duration_seconds: number | null;
  any_error: boolean | null;
  span_count: number;
  anchor_count: number;
  kinds: InteractionKinds | null;
  destination: InteractionDestination | null;
  http: InteractionHttp | null;
  /** Validated JWT subject of the caller; null when the call carried none. */
  principal_sub: string | null;
  /** a2a session id; null on every other protocol. */
  session_id: string | null;
}

/**
 * One-line rendering of the HTTP event for the detail panel — `POST → 200 (ok)`
 * — skipping whichever of the three facts is absent. Null when there is nothing
 * to show, so the panel omits the row rather than printing an empty one.
 */
export function httpSummary(http: InteractionHttp | null): string | null {
  if (!http) return null;
  const head = [http.method, http.status_code == null ? null : `→ ${http.status_code}`]
    .filter(Boolean)
    .join(' ');
  const tail = http.outcome ? `(${http.outcome})` : '';
  return [head, tail].filter(Boolean).join(' ') || null;
}

/** MCP protocol plumbing (lifecycle / tool discovery) — the rows the flow view
 *  hides by default. The vocabulary comes from the server's sanctioned
 *  classifier (which stamps `kinds` on each interaction row). */
export function isInfrastructure(ix: Pick<Interaction, 'kinds'>): boolean {
  const k = ix.kinds?.request_content_kind;
  return k === 'mcp_lifecycle_request' || k === 'tool_discovery_request';
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
