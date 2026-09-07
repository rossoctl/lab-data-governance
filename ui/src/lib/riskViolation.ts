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
 * Previous, `+1` for Next), clamped at both ends without wrapping.
 *
 * Clamping rather than wrapping: the Previous/Next controls disable at the
 * ends (`ViolationStepper`), so this only ever needs to hold the position
 * steady there; a wrapping Next would make "N of N" ambiguous about which
 * neighbour it means.
 */
export function stepViolation(current: number, delta: -1 | 1, count: number): number {
  const next = current + delta;
  if (next < 1) return 1;
  if (next > count) return count;
  return next;
}
