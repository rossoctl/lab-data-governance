/**
 * Row highlight: `data-dg-selected="active"` drives the background tint via
 * global.css for the single selected row. The attribute is omitted when the row
 * isn't selected, so unselected rows keep the default table styling.
 *
 * Single-sourced so the Entities, Interactions (tree) and flat-legs tables can't
 * drift on the attribute name or the omit-when-unselected rule.
 */
export function rowProps(isSelected: boolean) {
  return { 'data-dg-selected': isSelected ? 'active' : undefined };
}

/**
 * The label lineage overlay on a row: `data-dg-track="lit"` when one of the row's
 * legs carried a tracked value, `"dimmed"` when values are tracked and none did,
 * omitted when nothing is tracked (global.css). The same three states the
 * sequence diagram's rows and the graph's edges take, so the four views agree.
 */
export function trackProps(litEdgeIds: readonly string[] | null | undefined, edgeIds: readonly string[]) {
  if (litEdgeIds == null) return {};
  return { 'data-dg-track': edgeIds.some((id) => litEdgeIds.includes(id)) ? 'lit' : 'dimmed' };
}
