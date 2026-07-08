import { describe, it, expect } from 'vitest';
import { PinStore, colorForSlot, HIGHLIGHT_PALETTE } from './pins';

// Ported from trace_tree.html's TraceTreeNav multi-set highlight store: any
// number of pinned sets, each in the lowest free slot; freed slots are reused
// so a set's color stays stable across other sets' unpins. Slots 0-3 use the
// hand-tuned palette; slots >=4 are golden-angle HSL so they stay distinct.

describe('colorForSlot', () => {
  it('uses the hand-tuned palette for the first slots, HSL beyond', () => {
    expect(colorForSlot(0)).toBe(HIGHLIGHT_PALETTE[0]);
    expect(colorForSlot(3)).toBe(HIGHLIGHT_PALETTE[3]);
    expect(colorForSlot(4)).toBe('hsl(190 65% 65%)'); // (4 * 137.5) % 360 = 190
  });
});

describe('PinStore', () => {
  it('assigns the lowest free slot and reports pinned state', () => {
    const s = new PinStore();
    s.addPin({ key: 'a', label: 'A', spanIds: ['s1'] });
    s.addPin({ key: 'b', label: 'B', spanIds: ['s2'] });
    expect(s.isPinned('a')).toBe(true);
    expect(s.slotColorFor('a')).toBe(colorForSlot(0));
    expect(s.slotColorFor('b')).toBe(colorForSlot(1));
    // next free color previews slot 2.
    expect(s.nextFreeColor()).toBe(colorForSlot(2));
  });

  it('reuses a freed slot so remaining sets keep their color', () => {
    const s = new PinStore();
    s.addPin({ key: 'a', label: 'A', spanIds: [] }); // slot 0
    s.addPin({ key: 'b', label: 'B', spanIds: [] }); // slot 1
    s.removePin('a'); // frees slot 0
    // b keeps slot 1; the next pin takes the freed slot 0, not slot 2.
    expect(s.slotColorFor('b')).toBe(colorForSlot(1));
    s.addPin({ key: 'c', label: 'C', spanIds: [] });
    expect(s.slotColorFor('c')).toBe(colorForSlot(0));
  });

  it('addPin is idempotent on a duplicate key', () => {
    const s = new PinStore();
    s.addPin({ key: 'a', label: 'A', spanIds: ['s1'] });
    const added = s.addPin({ key: 'a', label: 'A-again', spanIds: ['s2'] });
    expect(added).toBe(false);
    expect(s.getPins()).toHaveLength(1);
  });

  it('clearAll drops every pin', () => {
    const s = new PinStore();
    s.addPin({ key: 'a', label: 'A', spanIds: [] });
    s.addPin({ key: 'b', label: 'B', spanIds: [] });
    s.clearAll();
    expect(s.getPins()).toHaveLength(0);
    // slots start fresh at 0 again.
    s.addPin({ key: 'c', label: 'C', spanIds: [] });
    expect(s.slotColorFor('c')).toBe(colorForSlot(0));
  });

  it('getPins exposes key + color + label in slot order', () => {
    const s = new PinStore();
    s.addPin({ key: 'a', label: 'Alpha', spanIds: [] });
    s.addPin({ key: 'b', label: 'Beta', spanIds: [] });
    expect(s.getPins()).toEqual([
      { key: 'a', color: colorForSlot(0), label: 'Alpha' },
      { key: 'b', color: colorForSlot(1), label: 'Beta' },
    ]);
  });
});
