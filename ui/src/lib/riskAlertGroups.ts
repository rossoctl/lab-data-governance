import type { TraceRiskRecord } from '../risk-api/types';

/** Matches the server's `RISK_LEVEL_ORDER` (`risk/rules/catalog.py`) — most severe first. */
const RISK_LEVEL_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4,
};
const UNKNOWN_RISK_RANK = Object.keys(RISK_LEVEL_RANK).length;

function riskRank(level: string): number {
  return RISK_LEVEL_RANK[level] ?? UNKNOWN_RISK_RANK;
}

export interface TraceRiskGroup {
  traceId: string;
  /** The highest-`version` record for this trace — the group's current state. */
  summary: TraceRiskRecord;
  /** Every record for this trace, in the order received. */
  rows: TraceRiskRecord[];
}

/**
 * Group `GET /risk/traces` records by `trace_id` for the alerts/incidents
 * card (issue #169). Built against `/risk/traces` rather than a dedicated
 * `/risk/alerts` endpoint — see `AlertsCard.tsx`'s module comment for why.
 *
 * Trace risk records are immutably versioned (a recompute inserts a new row
 * rather than updating in place — same convention as `interaction_risk_records`),
 * so several rows can share one `trace_id`; each group's `summary` is the
 * row with the highest `version`, not merely the first one seen.
 *
 * Groups are ordered by risk severity (critical first), then by the
 * summary's `computed_at` descending (most recent first) — a stable,
 * deterministic ordering for a card with no user-facing sort control.
 */
export function groupTraceRisksByWorkflow(items: TraceRiskRecord[]): TraceRiskGroup[] {
  const byTrace = new Map<string, TraceRiskRecord[]>();
  for (const item of items) {
    const rows = byTrace.get(item.trace_id);
    if (rows) rows.push(item);
    else byTrace.set(item.trace_id, [item]);
  }

  const groups: TraceRiskGroup[] = [];
  for (const [traceId, rows] of byTrace) {
    const summary = rows.reduce((max, row) => (row.version > max.version ? row : max));
    groups.push({ traceId, summary, rows });
  }

  groups.sort((a, b) => {
    const rankDiff = riskRank(a.summary.trace_risk_level) - riskRank(b.summary.trace_risk_level);
    if (rankDiff !== 0) return rankDiff;
    return b.summary.computed_at.localeCompare(a.summary.computed_at);
  });

  return groups;
}
