import type { Entity } from './flow';

/**
 * Resolves an **Entity** natural key to the friendly `display_name` the rest of
 * the UI shows, for the **Data lineage** metadata's two string-valued elements
 * (`data_sources` and `entities`, ADR-0027).
 *
 * Lineage stores the *natural key* deliberately — it is the entity's identity,
 * and the qualified form (`tool:agent:(travel_advisor,travel-advisor):search_destinations`)
 * is what stops two same-named tools on different agents collapsing into one
 * source. That makes the stored value correct and the *rendered* value wrong: a
 * governance reader recognises `search_destinations`, not the qualified key. So
 * the key stays the identity on the wire and in the React key, and this module
 * only supplies the label to put in front of a reader's eyes.
 *
 * Pure and render-free (this repo keeps such logic in `lib/`) so the fallback
 * rule below is testable without a DOM, and so the flow tables and the lineage
 * block cannot drift about what an entity is *called*.
 */

/**
 * `natural_key → display_name` for one trace's entity set.
 *
 * Built from the entity list rather than looked up per row so a lineage block
 * with N sources does N map hits, not N linear scans.
 *
 * Two facts about the input drive the shape:
 *
 *  - **Entities load asynchronously**, so `entities` is legitimately
 *    `undefined` (in flight) or short (a trace whose entity read has not landed,
 *    or an older row naming an entity the current set lacks). Both yield a map
 *    that simply misses — never a throw.
 *  - **A blank `display_name` is not a name.** It is skipped so the caller's
 *    fallback puts the natural key on screen rather than an empty label, the
 *    same judgement `lib/graph.ts` makes for a node's label.
 *
 * Note the *reverse* direction (display_name → key) is deliberately not offered:
 * display names are not unique (`create_booking` exists on more than one agent
 * in the demo trace), so only this direction is a function.
 */
export function displayNamesByKey(entities: Entity[] | undefined): Map<string, string> {
  const byKey = new Map<string, string>();
  for (const e of entities ?? []) {
    if (e.natural_key && e.display_name) byKey.set(e.natural_key, e.display_name);
  }
  return byKey;
}

/**
 * The label to render for one lineage natural key, and whether it differs from
 * the key itself.
 *
 * `qualified` is what tells the caller a tooltip is *needed*: when the key did
 * not resolve, the label already IS the key and a `title` repeating it is noise;
 * when it did, the friendly label alone is ambiguous (two agents' tools can share
 * a `display_name`), so the full key has to stay reachable or the UI makes two
 * distinct sources look identical. A governance surface must not do that.
 */
export function lineageLabel(
  naturalKey: string,
  byKey: Map<string, string>,
): { label: string; qualified: boolean } {
  const display = byKey.get(naturalKey);
  // Falling back to the key is the honest answer for an unresolved entity: it is
  // the identity the lineage row actually asserts. Rendering blank (or
  // "undefined") would hide a source that genuinely exists.
  if (!display) return { label: naturalKey, qualified: false };
  return { label: display, qualified: true };
}
