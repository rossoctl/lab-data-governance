import { describe, it, expect } from 'vitest';
import { formatTime24Utc } from './recentTraces';

// Ported from the retired Node-driven test_recent_traces_logic_js.py: the
// recent-traces "Started" column shows a zero-padded 24-hour UTC clock, and
// falls back to the raw input when it isn't a parseable date.
describe('formatTime24Utc', () => {
  it('renders a zero-padded 24-hour UTC clock and passes through non-dates', () => {
    expect(formatTime24Utc('2026-05-01T00:05:09Z')).toBe('00:05:09');
    expect(formatTime24Utc('2026-05-01T13:45:30.123456+00:00')).toBe('13:45:30');
    expect(formatTime24Utc('not-a-date')).toBe('not-a-date');
  });
});
