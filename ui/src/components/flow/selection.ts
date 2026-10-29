import type { SpanEvidence } from '../../types';

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
  /** Each leg's own content kind (the role its body played). The payload
   *  resource is bytes-only — the same hash can sit under two kinds on two
   *  legs — so the kind travels with the leg, not with the fetched payload. */
  requestContentKind: string | null;
  responseContentKind: string | null;
}

