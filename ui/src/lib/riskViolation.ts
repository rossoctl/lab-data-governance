/**
 * `?violation=` URL param semantics for the trace detail view (issue #170).
 *
 * 1-based, default omitted from the URL — the same convention
 * `RiskDashboardPage.tsx` documents for `?window=`: violation 1 is the bare
 * URL, `?violation=2`/`?violation=3` name the others, and stepping back to
 * violation 1 DELETES the param rather than writing it explicitly, so the
 * canonical URL for the first violation stays clean. Mirrors
 * `parseRiskWindow`'s coerce-never-throw contract (`lib/riskWindow.ts`):
 * anything the page can't act on reads as the default instead of crashing
 * the view.
 */

/** The default (and un-parameterized) violation position. */
export const DEFAULT_VIOLATION = 1;

/**
 * Coerce a raw `?violation=` value to a 1-based violation index, given how
 * many violations this trace actually has.
 *
 * Non-numeric, fractional, zero, negative, and out-of-range values all read
 * as {@link DEFAULT_VIOLATION} — never thrown, matching `parseRiskWindow`'s
 * treatment of a bad `?window=`. `count === 0` (no violations at all) has no
 * valid position, so it returns `null` rather than a `1` the caller would
 * have nothing to show for.
 */
export function parseViolationIndex(
  raw: string | null | undefined,
  count: number,
): number | null {
  if (count <= 0) return null;
  if (raw == null) return DEFAULT_VIOLATION;
  if (!/^\d+$/.test(raw)) return DEFAULT_VIOLATION;
  const n = Number(raw);
  if (n < 1 || n > count) return DEFAULT_VIOLATION;
  return n;
}

/**
 * Step the current 1-based violation position by `delta` (`-1` for
 * Previous, `+1` for Next), wrapping at both ends: Next on the last event goes
 * to the first, Previous on the first to the last. With one event both land on
 * it, so the stepper is never dead — a step is how the bar reopens a closed
 * panel on a trace's only event.
 */
export function stepViolation(current: number, delta: -1 | 1, count: number): number {
  return ((current - 1 + delta + count) % count) + 1;
}

/**
 * The old risk trace page's reading of a trace (`/risk/traces/<id>`, now a
 * redirect), as the trace page's risk layer spells it: `?risk=1`, the same
 * `?violation=`, and the old page's `?interaction=` as the trace page's `?iid=`.
 */
export function traceLayerParams(riskPageParams: URLSearchParams): URLSearchParams {
  const out = new URLSearchParams();
  out.set('risk', '1');
  const violation = riskPageParams.get('violation');
  if (violation != null) out.set('violation', violation);
  const interaction = riskPageParams.get('interaction');
  if (interaction != null) out.set('iid', interaction);
  return out;
}

/**
 * Where a trace's risk is read: the trace page's Execution Flow with the Risk
 * layer on — the graph is the view that shows both the risk colours and the
 * classification tags. `riskPageParams` are an old risk-page URL's, translated.
 */
export function traceRiskPath(traceId: string, riskPageParams = new URLSearchParams()): string {
  return `/traces/${encodeURIComponent(traceId)}/graph?${traceLayerParams(riskPageParams).toString()}`;
}
