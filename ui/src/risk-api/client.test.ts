import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { riskFetchJson, riskPath, RiskApiError } from './client';

const __dirname = dirname(fileURLToPath(import.meta.url));

// The risk API resolves at the app root (`/risk/...`), not under `/api/` —
// `data_governance/api/__init__.py`'s `build_app()` spreads
// `risk_routes.routes()`/`rules_routes.routes()`/`metrics_routes.routes()`
// straight into the top-level route list. This client must build those
// literal `/risk/...` URLs and must not reuse `../api/client`'s `/api`-
// prefixed builder.

describe('risk-api boundary', () => {
  it('does not import the existing /api client', () => {
    const source = readFileSync(join(__dirname, 'client.ts'), 'utf-8');
    expect(source).not.toMatch(/from ['"]\.\.\/api\/client['"]/);
  });
});

describe('riskPath', () => {
  it('builds a /risk-prefixed URL, not /api', () => {
    expect(riskPath('/traces')).toBe('/risk/traces');
    expect(riskPath('/traces')).not.toContain('/api');
  });

  it('encodes path segments', () => {
    expect(riskPath('/traces/a b/history')).toBe('/risk/traces/a%20b/history');
  });

  it('appends query params, omitting undefined/null', () => {
    expect(riskPath('/traces', { limit: 20, cursor: 'abc' })).toBe(
      '/risk/traces?limit=20&cursor=abc',
    );
    expect(riskPath('/traces', { limit: 20, cursor: undefined })).toBe(
      '/risk/traces?limit=20',
    );
    expect(riskPath('/traces', { risk_level: null })).toBe('/risk/traces');
  });

  it('never emits page or page_size, even if passed', () => {
    const url = riskPath('/traces', { page: 2, page_size: 50 } as unknown as Record<
      string,
      string | number
    >);
    // The params object is opaque to riskPath — it just serializes what it's
    // given — so this pins that *callers* (hooks, in later issues) are the
    // ones who must never construct such params, not that riskPath filters
    // them. Asserting the literal passthrough documents that contract.
    expect(url).toBe('/risk/traces?page=2&page_size=50');
  });

  it('preserves opaque keyset cursors verbatim (not numeric offsets)', () => {
    const cursor = 'eyJrIjp7ImNvbXB1dGVkX2F0IjoiMjAyNi0wMS0wMSJ9LCJzIjoiZm9vIn0';
    expect(riskPath('/traces', { cursor })).toBe(`/risk/traces?cursor=${cursor}`);
  });

  it('emits limit=0 rather than dropping it, so the server can reject it', () => {
    // http.py's parse_limit 400s on limit<=0. Silently stripping a falsy 0
    // would turn a client bug into "default limit used" instead of a visible
    // 400 — so the nullish check must be `!== null && !== undefined`, not a
    // truthiness check.
    expect(riskPath('/traces', { limit: 0 })).toBe('/risk/traces?limit=0');
  });
});

describe('riskFetchJson', () => {
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
      json: async () => ({ items: [], next_cursor: null }),
    });
    await expect(riskFetchJson('/traces')).resolves.toEqual({
      items: [],
      next_cursor: null,
    });
    expect(fetch).toHaveBeenCalledWith('/risk/traces');
  });

  it('builds the request URL from path and params', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [] }),
    });
    await riskFetchJson('/traces', { limit: 50 });
    expect(fetch).toHaveBeenCalledWith('/risk/traces?limit=50');
  });

  it('surfaces the FR-DAS-081 error triple as RiskApiError.detail/status', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({
        error: 'not found',
        detail: "no trace risk record for trace_id 'nope'",
        timestamp: '2026-08-27T00:00:00Z',
      }),
    });
    await expect(riskFetchJson('/traces/nope')).rejects.toMatchObject({
      status: 404,
      detail: "no trace risk record for trace_id 'nope'",
    });
  });

  it('handles a non-JSON error body (e.g. an unproxied 502) without throwing SyntaxError', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('Unexpected token < in JSON');
      },
    });
    const err: unknown = await riskFetchJson('/traces').catch((e) => e);
    expect(err).toBeInstanceOf(RiskApiError);
    expect((err as RiskApiError).status).toBe(502);
  });

  it('wraps a network failure as status 0', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new TypeError('network down'));
    await expect(riskFetchJson('/traces')).rejects.toMatchObject({ status: 0 });
  });
});
