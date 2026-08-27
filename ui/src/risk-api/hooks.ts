/**
 * Risk-API hook scaffold (issue #165).
 *
 * Deliberately hook-free: the issue asks for the client and routing shell,
 * not endpoint hooks for views nobody has built yet — pre-building
 * `useX`-style hooks here would guess at query shapes #166/#167/#168 haven't
 * settled. What IS pinned here is the query-key convention those three
 * issues will all need, since three issues writing hooks independently and
 * each picking its own cache root/key shape would make the risk subtree
 * un-invalidatable as a whole (`queryClient.invalidateQueries({queryKey:
 * RISK_QUERY_KEY_ROOT})` needs every risk key nested under the same root).
 */

/** Shared root every risk query key nests under. */
export const RISK_QUERY_KEY_ROOT = ['risk'] as const;

/**
 * The server's uniform default list-page size
 * (`API_RISK_{RULES,INTERACTIONS,TRACES}_DEFAULT_LIMIT` in
 * `data_governance/risk/config.py`), read here rather than guessed so a
 * hook can pass it explicitly instead of relying on the server's default.
 */
export const RISK_PAGE_SIZE = 50;

/** Build a query key nested under {@link RISK_QUERY_KEY_ROOT}. */
export function riskQueryKey(...segments: unknown[]): readonly unknown[] {
  return [...RISK_QUERY_KEY_ROOT, ...segments];
}
