/**
 * The flat view's request↔response connector: the SVG bracket that ties a
 * request leg's row to its response leg's row across the (usually
 * non-adjacent) rows between them.
 */

/** One interaction bracket's drawing role on a given flat-view row. */
export interface ConnectorRole {
  id: string;
  role: 'top' | 'bottom' | 'through';
}

/**
 * A deterministic muted color for an interaction's request↔response connector
 * line in the flat view. Hashing `ix.id` to a hue (NOT Math.random) keeps a
 * given interaction's bracket a stable color across renders and lets several
 * overlapping brackets be told apart. Kept dim (low saturation / mid lightness)
 * to sit alongside the view's muted grays (--dg-tree-guide / --dg-color-muted)
 * without shouting.
 */
export function connectorColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 45%, 60%)`;
}
