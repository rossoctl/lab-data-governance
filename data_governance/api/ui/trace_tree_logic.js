/*
 * Pure data-layer helpers for the trace-tree view (issue #14).
 *
 * Factored out of trace_tree.html so they can be exercised from Node
 * (tests/api/test_trace_tree_logic_js.py) without booting a browser.
 *
 * - buildParentIndex: turns a flat list of loaded spans into a
 *   {(trace_id|span_id): parent_id} map. The (trace_id, span_id)
 *   composite key matches PROJECT.md §3 (span_id is only locally
 *   unique within a trace).
 *
 * - descendantErrorAncestors: returns the set of (trace_id, span_id)
 *   keys for every loaded ancestor of an `error === true` span.
 *   Walks only the loaded set, so an error span inside a *collapsed*
 *   subtree does NOT propagate a badge to its ancestors — the v1
 *   limitation called out in PROJECT.md §8 / issue #14. The badge
 *   appears progressively as the user expands subtrees.
 */

'use strict';

function _key(traceId, spanId) {
  return traceId + '|' + spanId;
}

function buildParentIndex(spans) {
  const idx = {};
  for (const s of spans) {
    idx[_key(s.trace_id, s.span_id)] = s.parent_id;
  }
  return idx;
}

function descendantErrorAncestors(spans, parentByChild) {
  /* Walk up from every error===true span, collecting the (trace_id,
     span_id) keys of every loaded ancestor. Only ancestors whose row
     is in the loaded set get marked — descendants in collapsed
     subtrees are invisible (v1 limitation). */
  const flagged = new Set();

  // Build a span_id -> trace_id helper from the loaded set so we can
  // resolve each parent_id back to its (trace_id, span_id) key. Within
  // one trace tree all spans share trace_id; we still key by both for
  // future-proofing the helper against multi-trace lists.
  const traceIdBySpanId = new Map();
  for (const s of spans) {
    traceIdBySpanId.set(s.span_id, s.trace_id);
  }

  for (const s of spans) {
    if (s.error !== true) continue;
    let cursor = s.parent_id;
    while (cursor !== null && cursor !== undefined) {
      const traceId = traceIdBySpanId.get(cursor);
      if (traceId === undefined) {
        // Parent not in the loaded set — stop walking. The user has
        // not yet expanded this branch's intermediate ancestors so we
        // cannot legitimately mark them.
        break;
      }
      const k = _key(traceId, cursor);
      if (flagged.has(k)) {
        // Already walked this chain via another error span; stop to
        // avoid redundant work.
        break;
      }
      flagged.add(k);
      cursor = parentByChild[k];
    }
  }
  return flagged;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildParentIndex, descendantErrorAncestors };
}
if (typeof window !== 'undefined') {
  window.TraceTree = { buildParentIndex, descendantErrorAncestors };
}
