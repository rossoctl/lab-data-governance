/**
 * Thin same-origin fetch client for the `/api/` resource tree.
 *
 * The SPA and the API share an origin (ADR-0019: one process serves both), so
 * there's no base URL or CORS to manage — just build `/api/...` paths, parse
 * JSON on 2xx, and throw a typed error carrying the HTTP status otherwise so
 * hooks can treat 404 (absent resource) distinctly from other failures.
 */

export type QueryParams = Record<string, string | number | null | undefined>;

/** An API failure carrying the HTTP status (0 for a network/parse error). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Build an `/api`-prefixed URL, encoding path segments and appending params. */
export function apiPath(path: string, params?: QueryParams): string {
  const encoded = path
    .split('/')
    .map((seg) => (seg === '' ? '' : encodeURIComponent(seg)))
    .join('/');
  let url = `/api${encoded.startsWith('/') ? '' : '/'}${encoded}`;
  if (params) {
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) usp.set(k, String(v));
    }
    const qs = usp.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

/** GET `/api{path}` and parse JSON; throw {@link ApiError} on a non-2xx. */
export async function fetchJson<T>(path: string, params?: QueryParams): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(apiPath(path, params));
  } catch (e) {
    throw new ApiError(0, `network error: ${String(e)}`);
  }
  if (!resp.ok) {
    let body = '';
    try {
      body = await resp.text();
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, `HTTP ${resp.status}${body ? `: ${body}` : ''}`);
  }
  return (await resp.json()) as T;
}
