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
