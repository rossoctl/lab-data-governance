import type { To } from 'react-router-dom';
import type { CrossedLevel, LabelOrigin, LineageHop } from '../types';

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

/** An origin leg in another trace: that trace's flow view, selecting the origin's interaction. */
export function otherTraceOriginHref(o: LabelOrigin): string {
  return `/traces/${o.origin_trace_id}/flow?iid=${encodeURIComponent(o.origin_interaction_id)}`;
}

export const LEVEL_WORD: Record<CrossedLevel, string> = { value_only: 'value-only', corroborated: 'corroborated' };

/** `crossed, value-only` / `crossed, corroborated, ambiguous`: how a crossed value is known. */
export function crossedText(c: { level: CrossedLevel; ambiguous: boolean }): string {
  return `crossed, ${LEVEL_WORD[c.level]}${c.ambiguous ? ', ambiguous' : ''}`;
}

/**
 * How the value from this origin is known at the leg read, when its route rests
 * on a crossing: the `crossed` of a chain's last hop (the leg read). `null` when
 * no chain says so — an origin inside the conversation, or a server without chains.
 * An origin in another trace is not asked here: a persistent entity's later call
 * in the same conversation crosses too, and only the chain says which.
 */
export function crossedOfOrigin(o: LabelOrigin): { level: CrossedLevel; ambiguous: boolean } | null {
  for (const chain of o.chains ?? []) {
    const here = chain[chain.length - 1];
    if (here?.crossed != null) return here.crossed;
  }
  return null;
}

/** `#seq caller → callee leg`, as far as the leg is still known. */
export function hopText(h: LineageHop): string {
  const who = h.caller != null || h.callee != null ? ` ${h.caller ?? '?'} → ${h.callee ?? '?'}` : '';
  return `${h.seq != null ? `#${h.seq}` : 'leg'}${who} ${h.leg_type}`;
}
