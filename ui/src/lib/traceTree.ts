/**
 * Pure data-layer helpers for the trace-tree view's descendant-error badge.
 *
 * Ported verbatim from the retired `data_governance/api/ui/trace_tree_logic.js`.
 * The walker only sees *loaded* spans, so an error span inside a collapsed
 * subtree does NOT propagate a badge to its ancestors — the v1 limitation. The
 * badge appears progressively as the user expands subtrees.
 */

/** The subset of a Span the badge walker needs. */
export interface TreeSpan {
  trace_id: string;
  span_id: string;
  parent_id: string | null;
  /** OTLP tri-state: true (error), false (ok), or null (unset). */
  error: boolean | null;
}

/** Composite key `(trace_id|span_id)` — span_id is only locally unique. */
export function spanKey(traceId: string, spanId: string): string {
  return `${traceId}|${spanId}`;
}

/** Turn a flat list of loaded spans into a `{(trace_id|span_id): parent_id}` map. */
export function buildParentIndex(spans: readonly TreeSpan[]): Record<string, string | null> {
  const idx: Record<string, string | null> = {};
  for (const s of spans) {
    idx[spanKey(s.trace_id, s.span_id)] = s.parent_id;
  }
  return idx;
}

/**
 * Return the set of `(trace_id|span_id)` keys for every loaded ancestor of an
 * `error === true` span. Walks only the loaded set; stops at any parent not yet
 * loaded (the user hasn't expanded that branch). The error span itself is never
 * included — that carries the per-span error badge, a visually distinct signal.
 */
export function descendantErrorAncestors(
  spans: readonly TreeSpan[],
  parentByChild: Record<string, string | null>,
): Set<string> {
  const flagged = new Set<string>();

  // span_id -> trace_id, so each parent_id resolves back to a composite key.
  // Within one trace tree all spans share trace_id; keyed by both for a
  // future multi-trace list.
  const traceIdBySpanId = new Map<string, string>();
  for (const s of spans) {
    traceIdBySpanId.set(s.span_id, s.trace_id);
  }

  for (const s of spans) {
    if (s.error !== true) continue;
    let cursor: string | null | undefined = s.parent_id;
    while (cursor !== null && cursor !== undefined) {
      const traceId = traceIdBySpanId.get(cursor);
      if (traceId === undefined) {
        // Parent not in the loaded set — stop walking; can't legitimately
        // mark ancestors the user hasn't expanded to.
        break;
      }
      const k = spanKey(traceId, cursor);
      if (flagged.has(k)) {
        // Already walked this chain via another error span; stop.
        break;
      }
      flagged.add(k);
      cursor = parentByChild[k];
    }
  }
  return flagged;
}
