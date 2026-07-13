import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fetchJson, apiPath } from './client';

// The API client is a thin same-origin fetch wrapper: it builds /api/ URLs,
// parses JSON on 2xx, and throws a typed error (carrying the status) otherwise
// so hooks can distinguish 404 from other failures.

describe('apiPath', () => {
  it('encodes path segments and joins query params', () => {
    expect(apiPath('/traces')).toBe('/api/traces');
    expect(apiPath('/traces/a b/spans')).toBe('/api/traces/a%20b/spans');
    expect(apiPath('/traces', { limit: 20, cursor: 5 })).toBe(
      '/api/traces?limit=20&cursor=5',
    );
    // undefined/null params are omitted.
    expect(apiPath('/traces', { limit: 20, cursor: undefined })).toBe(
      '/api/traces?limit=20',
    );
  });
});

describe('fetchJson', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns parsed JSON on a 200', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ traces: [] }),
    });
    await expect(fetchJson('/traces')).resolves.toEqual({ traces: [] });
  });

  it('throws an error carrying the status on a non-2xx', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 404,
      text: async () => 'not found',
    });
    await expect(fetchJson('/traces/nope')).rejects.toMatchObject({ status: 404 });
  });
});
