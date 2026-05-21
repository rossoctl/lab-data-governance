/*
 * Pure data-layer helpers for the recent-traces view (issue #13).
 *
 * Factored out of index.html so they can be exercised from Node
 * (tests/api/test_recent_traces_logic_js.py) without booting a browser.
 *
 * - dedupeByTraceId: keeps the highest-seq anchor per trace_id across
 *   pages, per PROJECT.md §7. The listing root for a trace may flip
 *   between an orphan and the real root as late spans arrive
 *   (ADR-0001) or its seq may advance via finalization (§3.2 /
 *   ADR-0004); the UI dedupes client-side, keeping the most recent
 *   anchor.
 * - applyMissingParentFilter: hides rows whose listing root is an
 *   orphan (parent_id non-null on the listing-root span) when the
 *   user toggles "hide missing-parent" on. Issue #13's filter toggle.
 */

'use strict';

function dedupeByTraceId(rows) {
  const byTrace = new Map();
  for (const row of rows) {
    const existing = byTrace.get(row.trace_id);
    if (existing === undefined || row.seq > existing.seq) {
      byTrace.set(row.trace_id, row);
    }
  }
  // Preserve the listing-root started_at desc order produced by the
  // server: sort descending on started_at, ties broken by trace_id for
  // determinism.
  return Array.from(byTrace.values()).sort((a, b) => {
    if (a.started_at < b.started_at) return 1;
    if (a.started_at > b.started_at) return -1;
    return a.trace_id < b.trace_id ? -1 : a.trace_id > b.trace_id ? 1 : 0;
  });
}

function applyMissingParentFilter(rows, hideMissingParent) {
  if (!hideMissingParent) return rows;
  return rows.filter((r) => r.parent_id === null);
}

// Pure Node access. The browser script also loads this file via a
// <script> tag and accesses the globals attached to window.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { dedupeByTraceId, applyMissingParentFilter };
}
if (typeof window !== 'undefined') {
  window.RecentTraces = { dedupeByTraceId, applyMissingParentFilter };
}
