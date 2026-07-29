import type { UseQueryResult } from '@tanstack/react-query';

import { legLineageKey } from '../../lib/flow';
import type { LineageState, SpanEvidence, TraceDataLineage } from '../../types';

/**
 * The flow view's single selected row, as the detail panel needs it: already
 * projected into display fields + the fetched span evidence, so the panel is a
 * pure renderer and only `FlowTables` knows how a row becomes a selection.
 */
export interface Selection {
  kind: 'interaction' | 'entity';
  id: string;
  /** The panel's promoted caption for this selection ('Entity' | 'Interaction'). */
  sectionTitle: 'Entity' | 'Interaction';
  fields: Array<[string, string]>;
  evidence: SpanEvidence[];
  pinKey: string;
  pinLabel: string;
  /** Payload content hashes (interactions only) so the panel can lazily fetch
   *  and show the request/response bodies — ported from the vanilla flow view's
   *  Req/Resp cells + showPayload(). Null when the interaction carried none. */
  requestPayloadHash: string | null;
  responsePayloadHash: string | null;
}

/**
 * This payload's {@link LineageState} out of the trace-scoped query, keyed by the
 * leg it sits on (ADR-0027 D5).
 *
 * A failed read is its own arm, taken FIRST: the map is empty on error, so
 * falling through would report every leg as "not yet computed" — telling the
 * reader to wait for a derivation the view never actually asked about. That is
 * the one conflation this function exists to prevent.
 *
 * The genuine not-yet-computed cases still collapse to one `'pending'`: the read
 * is in flight, the leg has no lineage row (including the whole empty-`legs`
 * not-yet-migrated response), or the row exists with `lineage: null`. Those are
 * the same statement to the reader — "we have not derived this yet" — and lumping
 * them keeps the view from mistaking any of them for a real derived-empty triple.
 */
export function lineageOfLeg(
  lineageQ: Pick<UseQueryResult<TraceDataLineage>, 'data' | 'isError'>,
  selection: Selection,
  legType: 'request' | 'response',
): LineageState {
  if (lineageQ.isError) return { kind: 'error' };
  // Guard the key's meaning rather than assume it: `Selection.id` is an
  // interaction id only for an interaction selection, and a lineage key built
  // from an entity id would silently miss (or worse, collide) — an entity
  // selection carries no payload hashes, so this branch is unreachable today and
  // stays that way by construction.
  if (selection.kind !== 'interaction') return { kind: 'pending' };
  const lineage = lineageQ.data?.byLeg.get(legLineageKey(selection.id, legType));
  return lineage == null ? { kind: 'pending' } : { kind: 'derived', lineage };
}
