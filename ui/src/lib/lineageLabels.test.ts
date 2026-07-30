import { describe, it, expect } from 'vitest';
import { displayNamesByKey, lineageLabel } from './lineageLabels';
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
