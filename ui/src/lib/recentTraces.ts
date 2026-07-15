/**
 * Pure data-layer helpers for the recent-traces view.
 *
 * Ported verbatim (behaviour-for-behaviour) from the retired
 * `data_governance/api/ui/recent_traces_logic.js` so the migration keeps the
 * exact dedupe / filter / time-format semantics the vanilla view had.
 */

/** A recent-traces row: the listing-root Span's fields the helpers key on. */
export interface TraceRow {
  trace_id: string;
  /** Watermark seq of the listing-root Span; may advance on finalization. */
  seq: number;
  /** null on a Real root; the missing parent's id on an Orphan span. */
  parent_id: string | null;
  /** ISO-8601 started_at of the listing-root Span (trace-clock). */
  started_at: string;
}

/**
 * Keep the highest-`seq` anchor per `trace_id` across paginated responses.
 *
 * The listing root for a trace may flip between an orphan and the real root as
 * late spans arrive, or its seq may advance via finalization; the UI dedupes
 * client-side, keeping the most recent anchor. Output preserves the server's
 * `started_at` DESC order, ties broken by `trace_id` for determinism.
 */
export function dedupeByTraceId<T extends TraceRow>(rows: readonly T[]): T[] {
  const byTrace = new Map<string, T>();
  for (const row of rows) {
    const existing = byTrace.get(row.trace_id);
    if (existing === undefined || row.seq > existing.seq) {
      byTrace.set(row.trace_id, row);
    }
  }
  return Array.from(byTrace.values()).sort((a, b) => {
    if (a.started_at < b.started_at) return 1;
    if (a.started_at > b.started_at) return -1;
    return a.trace_id < b.trace_id ? -1 : a.trace_id > b.trace_id ? 1 : 0;
  });
}

/**
 * Hide rows whose listing root is an orphan (non-null `parent_id`) when the
 * "hide missing-parent" toggle is on. Off → return all rows unchanged.
 */
export function applyMissingParentFilter<T extends Pick<TraceRow, 'parent_id'>>(
  rows: readonly T[],
  hideMissingParent: boolean,
): T[] {
  if (!hideMissingParent) return rows.slice();
  return rows.filter((r) => r.parent_id === null);
}

/**
 * Format an ISO timestamp as a zero-padded 24-hour UTC clock (`HH:MM:SS`).
 * Returns the raw input unchanged when it isn't a parseable date.
 */
export function formatTime24Utc(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toISOString().slice(11, 19);
  } catch {
    return iso;
  }
}
