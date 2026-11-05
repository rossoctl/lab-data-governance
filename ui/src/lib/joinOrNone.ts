/**
 * `arr.length === 0 ? 'none' : arr.join(sep)` — the empty-list fallback
 * repeated across every rules-catalog cell/section (categories, allowed
 * actions, conditions) so the catalog UI shows an explicit "none" rather
 * than an empty cell (#171's acceptance criteria). One place to change the
 * fallback string or separator convention instead of five call sites.
 */
export function joinOrNone(items: readonly string[], separator = ', '): string {
  return items.length === 0 ? 'none' : items.join(separator);
}
