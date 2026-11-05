import type { To } from 'react-router-dom';
import type { LineageHop } from '../types';

/**
 * Where a lineage hop links (`LabelLineage`). `sameTrace` is the page's own
 * selection convention (`?iid=` on the trace page, `?interaction=` on the risk
 * trace page); `null` renders the hop as plain text. A hop in another trace
 * always goes to that trace's flow view (`otherTraceHref`).
 */
export interface HopLinks {
  sameTrace: (hop: LineageHop) => To | null;
}

export const NO_HOP_LINKS: HopLinks = { sameTrace: () => null };

/** The other trace's flow view, selecting the hop's interaction. */
export function otherTraceHref(hop: LineageHop): string {
  return `/traces/${hop.trace_id}/flow?iid=${encodeURIComponent(hop.interaction_id)}`;
}

/** `#seq caller → callee leg`, as far as the leg is still known. */
export function hopText(h: LineageHop): string {
  const who = h.caller != null || h.callee != null ? ` ${h.caller ?? '?'} → ${h.callee ?? '?'}` : '';
  return `${h.seq != null ? `#${h.seq}` : 'leg'}${who} ${h.leg_type}`;
}
