import { describe, it, expect } from 'vitest';
import { DEFAULT_VIOLATION, parseViolationIndex, stepViolation, traceLayerParams } from './riskViolation';

describe('riskViolation', () => {
  describe('parseViolationIndex', () => {
    it('parses a valid numeric value', () => {
      expect(parseViolationIndex('2', 3)).toBe(2);
    });

    it('defaults on a null (bare-URL) value', () => {
      expect(parseViolationIndex(null, 3)).toBe(DEFAULT_VIOLATION);
    });

    it('defaults on an empty string', () => {
      expect(parseViolationIndex('', 3)).toBe(DEFAULT_VIOLATION);
    });

    it('defaults on non-numeric garbage', () => {
      expect(parseViolationIndex('abc', 3)).toBe(DEFAULT_VIOLATION);
    });

    it('defaults on zero', () => {
      expect(parseViolationIndex('0', 3)).toBe(DEFAULT_VIOLATION);
    });

    it('defaults on a negative value', () => {
      expect(parseViolationIndex('-1', 3)).toBe(DEFAULT_VIOLATION);
    });

    it('defaults on a fractional value', () => {
      expect(parseViolationIndex('2.5', 3)).toBe(DEFAULT_VIOLATION);
    });

    it('defaults on an out-of-range value', () => {
      expect(parseViolationIndex('99', 3)).toBe(DEFAULT_VIOLATION);
    });

    it('returns null when there are no violations at all', () => {
      expect(parseViolationIndex('1', 0)).toBeNull();
      expect(parseViolationIndex(null, 0)).toBeNull();
    });

    it('accepts the last valid index (boundary)', () => {
      expect(parseViolationIndex('3', 3)).toBe(3);
    });
  });

  describe('stepViolation', () => {
    it('steps forward', () => {
      expect(stepViolation(1, 1, 3)).toBe(2);
    });

    it('steps backward', () => {
      expect(stepViolation(2, -1, 3)).toBe(1);
    });

    it('clamps at the lower end without wrapping', () => {
      expect(stepViolation(1, -1, 3)).toBe(1);
    });

    it('clamps at the upper end without wrapping', () => {
      expect(stepViolation(3, 1, 3)).toBe(3);
    });
  });

  describe('traceLayerParams (the risk page → the trace page with the layer on)', () => {
    it('turns ?violation= and ?interaction= into ?risk=1&violation=&iid=', () => {
      expect(traceLayerParams(new URLSearchParams('violation=2&interaction=i7')).toString()).toBe('risk=1&violation=2&iid=i7');
    });

    it('is ?risk=1 alone for a bare risk page URL, and ignores params it does not translate', () => {
      expect(traceLayerParams(new URLSearchParams('')).toString()).toBe('risk=1');
      expect(traceLayerParams(new URLSearchParams('window=7d')).toString()).toBe('risk=1');
    });
  });
});
