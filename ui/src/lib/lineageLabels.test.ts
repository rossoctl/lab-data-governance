import { describe, it, expect } from 'vitest';
import { displayNamesByKey, entityIdsByKey, lineageLabel } from './lineageLabels';
import type { Entity } from './flow';

function entity(over: Partial<Entity> = {}): Entity {
  return {
    id: 'e1',
    kind: 'tool',
    natural_key: 'tool:agent:(travel_advisor,travel-advisor):search_destinations',
    display_name: 'search_destinations',
    detected_from: 'span',
    ...over,
  };
}

describe('displayNamesByKey', () => {
  it('maps each entity natural key to its display name', () => {
    const byKey = displayNamesByKey([
      entity(),
      entity({ id: 'e2', natural_key: 'user', display_name: 'user', kind: 'user' }),
    ]);
    expect(byKey.get('tool:agent:(travel_advisor,travel-advisor):search_destinations')).toBe(
      'search_destinations',
    );
    expect(byKey.get('user')).toBe('user');
  });

  it('tolerates an entity read that has not landed yet', () => {
    // The entities query is asynchronous, so `undefined` is a normal state for a
    // first paint — it must yield an empty map, not throw.
    expect(displayNamesByKey(undefined).size).toBe(0);
    expect(displayNamesByKey([]).size).toBe(0);
  });

  it('skips an entity whose display name is blank', () => {
    // A blank name is not a name; leaving it out of the map is what lets the
    // caller fall back to the natural key instead of rendering an empty label.
    const byKey = displayNamesByKey([entity({ display_name: '' })]);
    expect(byKey.size).toBe(0);
  });

  it('keeps two entities that share a display name separately addressable', () => {
    // `create_booking` genuinely exists on more than one agent in the demo trace.
    // Keying on the natural key is what preserves the distinction the qualified
    // form was introduced to make.
    const byKey = displayNamesByKey([
      entity({ id: 'a', natural_key: 'tool:agent:(x,booking-agent):create_booking', display_name: 'create_booking' }),
      entity({ id: 'b', natural_key: 'tool:agent:(y,other-agent):create_booking', display_name: 'create_booking' }),
    ]);
    expect(byKey.size).toBe(2);
    expect(byKey.get('tool:agent:(x,booking-agent):create_booking')).toBe('create_booking');
    expect(byKey.get('tool:agent:(y,other-agent):create_booking')).toBe('create_booking');
  });
});

describe('lineageLabel', () => {
  const byKey = displayNamesByKey([entity()]);

  it('returns the friendly display name for a key it can resolve', () => {
    const { label, qualified } = lineageLabel(
      'tool:agent:(travel_advisor,travel-advisor):search_destinations',
      byKey,
    );
    expect(label).toBe('search_destinations');
    // `qualified` is what tells the caller the visible text is now SHORTER than
    // the identity, so the full key has to stay reachable in a tooltip.
    expect(qualified).toBe(true);
  });

  it('falls back to the raw natural key when the entity set does not name it', () => {
    // Never blank, never "undefined": the key is the identity the lineage row
    // actually asserts, so it is the honest thing to show.
    const { label, qualified } = lineageLabel('tool:agent:(p,unknown-agent):mystery', byKey);
    expect(label).toBe('tool:agent:(p,unknown-agent):mystery');
    // Not qualified: the label already IS the key, so a title repeating it would
    // be noise.
    expect(qualified).toBe(false);
  });

  it('falls back for every key while the entity read is still in flight', () => {
    const empty = displayNamesByKey(undefined);
    expect(lineageLabel('user', empty)).toEqual({ label: 'user', qualified: false });
  });
});

/**
 * The `natural_key → entity.id` bridge. Tested at the definition rather than only
 * through the Lineage view, because it is the ONE place two different keyspaces meet
 * — data lineage's natural keys and the graph's entity ids — and getting it wrong
 * fails silently: a highlight that lights nothing looks exactly like an entity with
 * no sources.
 */
describe('entityIdsByKey', () => {
  it('maps each entity natural key to its id', () => {
    const { byKey, ambiguous } = entityIdsByKey([
      entity(),
      entity({ id: 'e2', natural_key: 'user', kind: 'user' }),
    ]);
    expect(byKey.get('tool:agent:(travel_advisor,travel-advisor):search_destinations')).toBe('e1');
    expect(byKey.get('user')).toBe('e2');
    expect(ambiguous).toEqual([]);
  });

  it('tolerates an entity read that has not landed yet', () => {
    // Same asynchrony as displayNamesByKey: `undefined` is a normal first paint and
    // must yield an empty map, not a throw.
    const { byKey, ambiguous } = entityIdsByKey(undefined);
    expect(byKey.size).toBe(0);
    expect(ambiguous).toEqual([]);
  });

  it('misses (rather than throws) for a key no entity carries', () => {
    // The expected case for a lineage source outside the trace's own entity set. The
    // caller discloses the miss; this function simply has no answer.
    const { byKey } = entityIdsByKey([entity()]);
    expect(byKey.get('service:(elsewhere,api)')).toBeUndefined();
  });

  it('skips a blank natural key rather than letting one entity answer for all of them', () => {
    // A blank key is not an identity. Mapping `''` would resolve every unkeyed
    // lineage row to whichever entity happened to be blank.
    const { byKey } = entityIdsByKey([entity({ id: 'blank', natural_key: '' }), entity()]);
    expect(byKey.has('')).toBe(false);
    expect(byKey.size).toBe(1);
  });

  it('keeps two DIFFERENT keys apart even when their display names collide', () => {
    // The distinguishing case against the reverse direction this module refuses:
    // `create_booking` exists on two agents, so display_name → key is not a function
    // — but the qualified keys are distinct and each maps to its own entity.
    const { byKey, ambiguous } = entityIdsByKey([
      entity({ id: 'a', natural_key: 'tool:agent:(x,booking-agent):create_booking', display_name: 'create_booking' }),
      entity({ id: 'b', natural_key: 'tool:agent:(y,other-agent):create_booking', display_name: 'create_booking' }),
    ]);
    expect(byKey.get('tool:agent:(x,booking-agent):create_booking')).toBe('a');
    expect(byKey.get('tool:agent:(y,other-agent):create_booking')).toBe('b');
    // Distinct keys, so nothing is ambiguous — the collision is in the LABELS, which
    // this direction does not read.
    expect(ambiguous).toEqual([]);
  });

  it('resolves a duplicated key to the FIRST entity and reports the key', () => {
    // Should not happen (natural_key is the entity's identity, ADR-0013) — which is
    // why it is detected rather than assumed away. First-wins keeps the map a total
    // function so a highlight still resolves; the report is what stops the arbitrary
    // pick being silent.
    const { byKey, ambiguous } = entityIdsByKey([
      entity({ id: 'first', natural_key: 'agent:(p,twin)' }),
      entity({ id: 'second', natural_key: 'agent:(p,twin)' }),
    ]);
    expect(byKey.get('agent:(p,twin)')).toBe('first');
    expect(ambiguous).toEqual(['agent:(p,twin)']);
  });

  it('reports a thrice-claimed key ONCE, not once per extra claimant', () => {
    // The count is "how many identities are in doubt", not "how badly the duplication
    // went" — the same one-entry-per-defect rule lib/graph's `dropped` follows.
    const { ambiguous } = entityIdsByKey([
      entity({ id: 'a', natural_key: 'agent:(p,twin)' }),
      entity({ id: 'b', natural_key: 'agent:(p,twin)' }),
      entity({ id: 'c', natural_key: 'agent:(p,twin)' }),
    ]);
    expect(ambiguous).toEqual(['agent:(p,twin)']);
  });

  it('is deterministic: entities order decides the winner, so a reload agrees', () => {
    const entities = [
      entity({ id: 'first', natural_key: 'agent:(p,twin)' }),
      entity({ id: 'second', natural_key: 'agent:(p,twin)' }),
    ];
    for (let i = 0; i < 5; i += 1) {
      expect(entityIdsByKey(entities).byKey.get('agent:(p,twin)')).toBe('first');
    }
  });
});
