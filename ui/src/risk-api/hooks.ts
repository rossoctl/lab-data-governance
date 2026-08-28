/**
 * Risk-API hooks (issue #165's scaffold, extended by #169's dashboard).
 *
 * #165 deliberately shipped hook-free, pinning only the query-key
 * convention every risk hook must nest under (`RISK_QUERY_KEY_ROOT`) so the
 * whole subtree stays invalidatable as one
 * (`queryClient.invalidateQueries({queryKey: RISK_QUERY_KEY_ROOT})`). The
 * dashboard hooks below are the first hooks built against that convention.
 */
import {
  useQuery,
  useInfiniteQuery,
  type UseQueryResult,
  type UseInfiniteQueryResult,
  type InfiniteData,
} from '@tanstack/react-query';
import { riskFetchJson } from './client';
import type {
  RiskSummary,
  RiskDistributionResponse,
  RiskByCategoryResponse,
  TopRulesResponse,
  TraceRiskListResponse,
} from './types';

/** Shared root every risk query key nests under. */
export const RISK_QUERY_KEY_ROOT = ['risk'] as const;

/**
 * The dashboard's shared page/limit size for list and top-N endpoints. Was
 * originally pinned to the server's own default
 * (`API_RISK_{RULES,INTERACTIONS,TRACES}_DEFAULT_LIMIT` in
 * `data_governance/risk/config.py`, 50) but the dashboard's cards are compact
 * summaries, not full listings, so it is now a deliberate UI-side override
 * passed explicitly rather than left to the server's default.
 */
export const RISK_PAGE_SIZE = 10;

/** Build a query key nested under {@link RISK_QUERY_KEY_ROOT}. */
export function riskQueryKey(...segments: unknown[]): readonly unknown[] {
  return [...RISK_QUERY_KEY_ROOT, ...segments];
}

/** FR-DAS-050a summary tiles. `GET /risk/metrics/summary?window=`. */
export function useRiskSummary(window: string): UseQueryResult<RiskSummary> {
  return useQuery({
    queryKey: riskQueryKey('metrics', 'summary', { window }),
    queryFn: () => riskFetchJson<RiskSummary>('/metrics/summary', { window }),
  });
}

/** FR-DAS-050 risk distribution. `GET /risk/metrics/risk-distribution?window=`. */
export function useRiskDistribution(window: string): UseQueryResult<RiskDistributionResponse> {
  return useQuery({
    queryKey: riskQueryKey('metrics', 'risk-distribution', { window }),
    queryFn: () =>
      riskFetchJson<RiskDistributionResponse>('/metrics/risk-distribution', { window }),
  });
}

/** FR-DAS-054 risk by category. `GET /risk/metrics/risk-by-category?window=`. */
export function useRiskByCategory(window: string): UseQueryResult<RiskByCategoryResponse> {
  return useQuery({
    queryKey: riskQueryKey('metrics', 'risk-by-category', { window }),
    queryFn: () =>
      riskFetchJson<RiskByCategoryResponse>('/metrics/risk-by-category', { window }),
  });
}

/**
 * FR-DAS-051 top rules. `GET /risk/metrics/top-rules?window=&limit=`. A
 * bounded top-N ranking, not a paginated list — no cursor, matching
 * `aggregate.get_top_rules`/`metrics_routes.py`'s response shape.
 */
export function useTopRules(window: string, limit?: number): UseQueryResult<TopRulesResponse> {
  return useQuery({
    queryKey: riskQueryKey('metrics', 'top-rules', { window, limit }),
    queryFn: () =>
      riskFetchJson<TopRulesResponse>('/metrics/top-rules', { window, limit }),
  });
}

/**
 * Alerts/incidents card data source (issue #169): `GET /risk/traces`,
 * sorted by risk-level severity, keyset-paginated via the server's own
 * `next_cursor` — unlike `useTracesInfinite`'s short-page heuristic, this
 * endpoint returns an explicit cursor, so paging stops exactly when the
 * server says there's no more.
 */
export function useRiskTracesInfinite(
  window: string,
): UseInfiniteQueryResult<InfiniteData<TraceRiskListResponse>> {
  return useInfiniteQuery({
    queryKey: riskQueryKey('traces', { window }),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      riskFetchJson<TraceRiskListResponse>('/traces', {
        window,
        sort: 'risk_level_desc',
        limit: RISK_PAGE_SIZE,
        ...(pageParam !== undefined ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}
