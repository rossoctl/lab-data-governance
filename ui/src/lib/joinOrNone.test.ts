import { describe, it, expect } from 'vitest';
import { joinOrNone } from './joinOrNone';

describe('joinOrNone', () => {
  it('joins non-empty items with the default separator', () => {
    expect(joinOrNone(['a', 'b', 'c'])).toBe('a, b, c');
  });

  it('joins with a custom separator', () => {
    expect(joinOrNone(['a', 'b'], '; ')).toBe('a; b');
  });

  it('returns "none" for an empty array', () => {
    expect(joinOrNone([])).toBe('none');
  });

  it('returns the single item unchanged, without a separator', () => {
    expect(joinOrNone(['solo'])).toBe('solo');
  });
});
