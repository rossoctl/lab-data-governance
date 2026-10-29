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

/**
 * The risk levels that constitute an alert (issue #214) — everything from
 * `low` upwards, i.e. `RISK_LEVEL_RANK` minus `none`. Passed to
 * `GET /risk/traces?risk_level=` as a CSV so the server does the filtering
 * (`retrieval.list_trace_risk` applies `risk_level` AFTER its
 * latest-version-per-trace reduction, which is exactly the semantics this
 * card needs), keeping keyset pagination honest: a page of `RISK_PAGE_SIZE`
 * is that many alerts rather than mostly none-risk rows the client discards.
 */
export const ALERT_RISK_LEVELS = ['critical', 'high', 'medium', 'low'] as const;

/**
 * Is this level "no risk at all"? Only a literal `none` is — an
 * unrecognised level is deliberately NOT treated as none, because
 * `trace_risk_level` is TEXT sourced from OPA (implementation-notes-v3
 * §3.3), so a level this UI has not been taught about is far more likely a
 * new severity than an absence of risk. Hiding it would silently drop a real
 * alert; showing it (unranked, so sorted last) merely looks unfamiliar.
 */
export function isNoRisk(level: string | null | undefined): boolean {
  return (level ?? '').trim().toLowerCase() === 'none';
}

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
 *
 * Traces whose CURRENT risk level is `none` are omitted entirely (issue
 * #214): an alerts table lists things needing attention, and a trace that
 * was evaluated and found clean is not one. "Current" matters — the check
 * runs on the group's `summary` (highest version), so a trace downgraded to
 * `none` by a recompute drops out, and one upgraded off `none` appears, each
 * on the strength of its latest state rather than any historical row. A kept
 * group's `rows` still carry its full history, `none` versions included:
 * filtering removes whole groups, never rows within one.
 *
 * The server is asked to apply the same filter ({@link ALERT_RISK_LEVELS}),
 * so in practice this is belt-and-braces — it also covers records reaching
 * the card from anywhere else (a cached page, a future `/risk/alerts` swap).
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
    if (isNoRisk(summary.trace_risk_level)) continue;
    groups.push({ traceId, summary, rows });
  }

  groups.sort((a, b) => {
    const rankDiff = riskRank(a.summary.trace_risk_level) - riskRank(b.summary.trace_risk_level);
    if (rankDiff !== 0) return rankDiff;
    return b.summary.computed_at.localeCompare(a.summary.computed_at);
  });

  return groups;
}
