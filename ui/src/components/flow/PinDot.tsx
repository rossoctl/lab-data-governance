/** The highlight-set dot a pinned flow-table row carries, in that pin's slot color. */
export function PinDot({ color }: { color: string | undefined }) {
  if (!color) return null;
  return (
    <span
      aria-label="pinned"
      style={{ display: 'inline-block', width: 9, height: 9, borderRadius: '50%', background: color }}
    />
  );
}
