/**
 * Thin same-origin fetch client for the `/risk/` resource tree (issue #165).
 *
 * `data_governance/api/__init__.py`'s `build_app()` registers every risk
 * route (`risk_routes`, `rules_routes`, `metrics_routes`) at the app root
 * with literal `/risk/...` paths — not under `/api/`. This client mirrors
 * the *shape* of `../api/client` (same same-origin-fetch idiom, same typed
 * error carrying the HTTP status) but intentionally does not import from
 * it: the two resource trees have different URL prefixes and different
 * error-body shapes (see {@link RiskApiError}), so sharing code here would
 * mean one of them silently inheriting behavior meant for the other.
 */

export type RiskQueryParams = Record<string, string | number | null | undefined>;

/**
 * A risk-API failure. Unlike `../api/client`'s `ApiError` (message-only),
 * every risk API error body is the FR-DAS-081 triple
 * `{error, detail, timestamp}` (`data_governance/risk/api/http.py`'s
 * `error_response`), so `detail` is carried separately rather than folded
 * into the message string. `status` is `0` for a network/parse failure, or
 * whenever the error body wasn't the expected JSON triple (e.g. a 502 from
 * an unproxied dev server serving an HTML error page).
 */
export class RiskApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: string,
  ) {
    super(message);
    this.name = 'RiskApiError';
  }
}

/** Build a `/risk`-prefixed URL, encoding path segments and appending params. */
export function riskPath(path: string, params?: RiskQueryParams): string {
  const encoded = path
    .split('/')
    .map((seg) => (seg === '' ? '' : encodeURIComponent(seg)))
    .join('/');
  let url = `/risk${encoded.startsWith('/') ? '' : '/'}${encoded}`;
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

/** GET `/risk{path}` and parse JSON; throw {@link RiskApiError} on a non-2xx. */
export async function riskFetchJson<T>(
  path: string,
  params?: RiskQueryParams,
): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(riskPath(path, params));
  } catch (e) {
    throw new RiskApiError(0, `network error: ${String(e)}`);
  }
  if (!resp.ok) {
    // FR-DAS-081 error bodies are always the {error, detail, timestamp}
    // triple, but a response never reaching the app (e.g. a proxy 502
    // returning an HTML page) won't parse as JSON — fall back to a
    // detail-less error rather than letting a SyntaxError escape.
    let error = '';
    let detail: string | undefined;
    try {
      const body = (await resp.json()) as { error?: string; detail?: string };
      error = body.error ?? '';
      detail = body.detail;
    } catch {
      /* non-JSON error body; leave error/detail unset */
    }
    throw new RiskApiError(
      resp.status,
      `HTTP ${resp.status}${error ? `: ${error}` : ''}`,
      detail,
    );
  }
  return (await resp.json()) as T;
}
