import { describe, it, expect } from 'vitest';
import {
  dedupeByTraceId,
  applyMissingParentFilter,
  formatTime24Utc,
  type TraceRow,
} from './recentTraces';

// All cases ported verbatim from the retired Node-driven
// test_recent_traces_logic_js.py so the migration preserves the exact
// dedupe / filter / time-format behaviour the vanilla view shipped.

describe('formatTime24Utc', () => {
  it('renders a zero-padded 24-hour UTC clock and passes through non-dates', () => {
    expect(formatTime24Utc('2026-05-01T00:05:09Z')).toBe('00:05:09');
    expect(formatTime24Utc('2026-05-01T13:45:30.123456+00:00')).toBe('13:45:30');
    expect(formatTime24Utc('not-a-date')).toBe('not-a-date');
  });
});

describe('dedupeByTraceId', () => {
  it('keeps the highest-seq anchor per trace_id', () => {
    const rows: TraceRow[] = [
      { trace_id: 'A', seq: 1, parent_id: 'X', started_at: '2026-05-01T12:00:00Z' },
      { trace_id: 'A', seq: 5, parent_id: null, started_at: '2026-05-01T11:00:00Z' },
      { trace_id: 'B', seq: 2, parent_id: null, started_at: '2026-05-01T13:00:00Z' },
    ];
    const byTrace = Object.fromEntries(
      dedupeByTraceId(rows).map((r) => [r.trace_id, r]),
    );
    expect(byTrace.A.seq).toBe(5);
    expect(byTrace.A.parent_id).toBeNull();
    expect(byTrace.B.seq).toBe(2);
  });

  it('orders output by started_at desc (server sort preserved)', () => {
    const rows: TraceRow[] = [
      { trace_id: 'A', seq: 1, parent_id: null, started_at: '2026-05-01T10:00:00Z' },
      { trace_id: 'B', seq: 2, parent_id: null, started_at: '2026-05-01T13:00:00Z' },
      { trace_id: 'C', seq: 3, parent_id: null, started_at: '2026-05-01T11:00:00Z' },
    ];
    expect(dedupeByTraceId(rows).map((r) => r.trace_id)).toEqual(['B', 'C', 'A']);
  });
});

describe('applyMissingParentFilter', () => {
  it('returns all rows when the toggle is off', () => {
    const rows = [
      { parent_id: null },
      { parent_id: 'missing' },
    ];
    expect(applyMissingParentFilter(rows, false)).toHaveLength(2);
  });

  it('hides orphan listing roots when the toggle is on', () => {
    const rows: TraceRow[] = [
      { trace_id: 'A', seq: 1, parent_id: null, started_at: '2026-05-01T10:00:00Z' },
      { trace_id: 'B', seq: 2, parent_id: 'missing', started_at: '2026-05-01T11:00:00Z' },
      { trace_id: 'C', seq: 3, parent_id: null, started_at: '2026-05-01T12:00:00Z' },
    ];
    expect(applyMissingParentFilter(rows, true).map((r) => r.trace_id)).toEqual([
      'A',
      'C',
    ]);
  });
});
