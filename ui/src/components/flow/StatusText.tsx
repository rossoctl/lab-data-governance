/**
 * The tri-state error cell both interaction tables render — the tree table off
 * the interaction's aggregated `any_error`, the flat table off the leg's own
 * `error`.
 *
 * Three states, not two: `true` → ERROR, `false` → ok, and `null`/`undefined` →
 * an em dash meaning *unknown* (nothing has reported a status for this leg yet).
 * The dash must never render as `ok` — "we don't know" and "it succeeded" are
 * different facts.
 */
export function StatusText({ error }: { error: boolean | null | undefined }) {
  if (error === true) return <span style={{ color: 'var(--dg-color-error)' }}>ERROR</span>;
  if (error === false) return <span style={{ color: 'var(--dg-color-ok)' }}>ok</span>;
  return <>—</>;
}
