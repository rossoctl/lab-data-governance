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
 * `natural_key → entity.id` for one trace's entity set, plus every key that more
 * than one entity claimed.
 *
 * THE BRIDGE THE GRAPH NEEDS. Data lineage cites its sources by **natural key**
 * (ADR-0027 — the qualified identity, see this module's header) while the Execution
 * Flow graph's nodes are keyed on **entity id** (`lib/graph`'s `GraphNodeSpec.id`,
 * which is what an edge's source/target names). Highlighting a lineage source as a
 * node therefore needs this direction, and needs it as a MAP: the lineage view
 * resolves the union of many legs' sources, so a per-source linear scan of
 * `entities` would be quadratic in a trace's own size. Same reasoning as
 * {@link displayNamesByKey}, one module along, which is why it lives beside it
 * rather than in the graph modules — this module is exactly "translate a lineage
 * natural key into something the rest of the UI holds".
 *
 * NOT THE SAME RISK AS THE REVERSE `display_name → key` THIS MODULE REFUSES.
 * Display names are genuinely not unique, so that direction is not a function at
 * all. `natural_key` *should* be 1:1 with an entity: it is the entity's identity
 * (ADR-0013 makes an entity cross-trace-stable and keyed on exactly this), and the
 * server upserts on it. But "should" is not "is verified here", and the `Entity`
 * wire type carries no uniqueness guarantee this code can lean on — so the
 * collision is DETECTED rather than assumed away.
 *
 * WHEN A KEY COLLIDES, FIRST WINS AND THE KEY IS REPORTED. First-wins keeps the
 * map a total function (a highlight still resolves, so the reader is not left with
 * a blank answer), `entities` order makes the choice deterministic across reloads,
 * and `ambiguous` is what stops that arbitrary pick being a silent one: the caller
 * discloses it, exactly as `lib/graph` discloses a dropped interaction rather than
 * quietly drawing fewer arrows. The alternative — dropping a colliding key
 * entirely — was rejected because it turns a *data* problem into an apparently
 * missing source, which is the harder failure to notice.
 *
 * A blank `natural_key` is skipped: it is not an identity, and mapping `''` would
 * let one unrelated entity answer for every unkeyed lineage row.
 */
export function entityIdsByKey(entities: readonly Entity[] | undefined): {
  byKey: Map<string, string>;
  /** Natural keys claimed by more than one entity, in first-seen order. */
  ambiguous: string[];
} {
  const byKey = new Map<string, string>();
  const ambiguous: string[] = [];
  for (const e of entities ?? []) {
    if (!e.natural_key) continue;
    if (byKey.has(e.natural_key)) {
      // Recorded once per KEY, not once per extra claimant: three entities on one
      // key is one ambiguity to disclose, and counting claimants would make the
      // notice's number a function of how badly the duplication went rather than
      // of how many identities are in doubt.
      if (!ambiguous.includes(e.natural_key)) ambiguous.push(e.natural_key);
      continue; // first wins — see the doc-comment
    }
    byKey.set(e.natural_key, e.id);
  }
  return { byKey, ambiguous };
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
