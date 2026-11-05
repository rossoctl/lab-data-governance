/**
 * Risk dashboard window selector (issue #169). MVP scope offers only the
 * three fixed windows `http.resolve_window` accepts as non-custom
 * (`data_governance/risk/api/http.py`'s `WINDOW_DELTAS`) — no `custom` range
 * picker. `DEFAULT_RISK_WINDOW` matches the server's own `DEFAULT_WINDOW`
 * so the canonical URL carries no `?window=` param (the delete-the-default
 * convention `RecentTracesPage` already uses for its own window key).
 */
export type RiskWindowKey = '24h' | '7d' | '30d';

export const RISK_WINDOW_KEYS: readonly RiskWindowKey[] = ['24h', '7d', '30d'];

export const DEFAULT_RISK_WINDOW: RiskWindowKey = '24h';

/**
 * Coerce a `?window=` query value to a valid key, defaulting on anything the
 * server wouldn't accept as one of the three fixed windows — including
 * `custom` (out of MVP scope here) and any case mismatch, matching the
 * server's case-sensitive comparison.
 */
export function parseRiskWindow(raw: string | null): RiskWindowKey {
  return raw != null && (RISK_WINDOW_KEYS as readonly string[]).includes(raw)
    ? (raw as RiskWindowKey)
    : DEFAULT_RISK_WINDOW;
}
