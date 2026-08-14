/** The highlight-set dot a pinned flow-table row carries, in that pin's slot color. */
export function PinDot({ color }: { color: string | undefined }) {
  if (!color) return null;
  return (
    <span
      // `role="img"` is load-bearing, not decoration: an `aria-label` on a bare
      // <span> with no role is ignored by most screen readers, so the pin state was
      // announced nowhere — the column header is `screenReaderText="Pinned"` and the
      // cell itself contributed no text at all.
      role="img"
      aria-label="pinned"
      style={{ display: 'inline-block', width: 9, height: 9, borderRadius: '50%', background: color }}
    />
  );
}
