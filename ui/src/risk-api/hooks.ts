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
  RuleListResponse,
  RuleListItem,
  RuleCategoriesResponse,
  TraceRiskDetailResponse,
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

/** Optional `GET /risk/rules` filters (issue #171), matching #113's `category`/`risk_level` params. */
export interface RulesFilter {
  category?: string;
  riskLevel?: string;
}

/**
 * Rules catalog table (issue #171). `GET /risk/rules`, keyset-paginated via
 * the server's own `next_cursor` — same convention as
 * {@link useRiskTracesInfinite}. Filters are part of the query key so
 * switching category/risk_level starts a fresh paginated query rather than
 * reusing a stale cursor from a different filter set.
 */
export function useRulesInfinite(
  filter: RulesFilter,
): UseInfiniteQueryResult<InfiniteData<RuleListResponse>> {
  return useInfiniteQuery({
    queryKey: riskQueryKey('rules', filter),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      riskFetchJson<RuleListResponse>('/rules', {
        category: filter.category,
        risk_level: filter.riskLevel,
        ...(pageParam !== undefined ? { cursor: pageParam } : {}),
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

/**
 * Rule detail (issue #171). `GET /risk/rules/{rule_id}`. Disabled when
 * `ruleId` is undefined (e.g. a route mounted without a param yet) so no
 * request goes out for `/risk/rules/undefined`; a missing `rule_id` on a
 * real request 404s server-side and surfaces via `RiskApiError.status`,
 * left for the caller (`RiskRuleDetailPage`) to render as a not-found state.
 */
export function useRule(ruleId: string | undefined): UseQueryResult<RuleListItem> {
  return useQuery({
    queryKey: riskQueryKey('rules', ruleId),
    queryFn: () => riskFetchJson<RuleListItem>(`/rules/${ruleId}`),
    enabled: ruleId !== undefined,
  });
}

/** FR-DAS-061 category filter options. `GET /risk/rules/categories`. */
export function useRuleCategories(): UseQueryResult<RuleCategoriesResponse> {
  return useQuery({
    queryKey: riskQueryKey('rules', 'categories'),
    queryFn: () => riskFetchJson<RuleCategoriesResponse>('/rules/categories'),
  });
}

/**
 * Trace detail forest (issue #170). `GET /risk/traces/{trace_id}` — the
 * trace risk record plus its full interaction forest in one read (AC #1: no
 * N+1, no per-interaction refetch). Disabled when `traceId` is undefined,
 * mirroring {@link useRule}'s guard against a request for `/risk/traces/undefined`.
 *
 * Query key ends in `'detail'` to stay disjoint from
 * {@link useRiskTracesInfinite}'s `riskQueryKey('traces', { window })` — the
 * two must never collide in react-query's cache despite both nesting under
 * `riskQueryKey('traces', ...)`.
 */
export function useTraceRiskDetail(traceId: string | undefined): UseQueryResult<TraceRiskDetailResponse> {
  return useQuery({
    queryKey: riskQueryKey('traces', traceId, 'detail'),
    queryFn: () => riskFetchJson<TraceRiskDetailResponse>(`/traces/${traceId}`),
    enabled: traceId !== undefined,
  });
}

/**
 * The rule catalog as an id-indexed lookup (issue #170's `PolicyDecisionPanel`,
 * "prefer [one bulk read] to avoid N+1" over fetching each triggered rule by
 * id). ONE `GET /risk/rules` call with an explicit high `limit`, `select`ed
 * into a `Map<rule_id, RuleListItem>`.
 *
 * `/risk/rules` is cursor-paginated (issue #171); a catalog larger than one
 * page degrades a rule reference to its bare id — the panel still links to
 * `/risk/rules/{id}`, it just can't show that rule's `rule_name` inline.
 * Following the cursor to build a complete index would reintroduce the
 * multi-request cost this hook exists to avoid, for a cosmetic gain.
 *
 * Capped at the server's own ceiling (`API_RISK_RULES_MAX_LIMIT`,
 * `data_governance/risk/config.py`, default 200) rather than some larger
 * number: the server clamps `limit` to that value regardless, so requesting
 * more than it would ever serve in one page was dead intent, not a bigger
 * page.
 */
const RULE_CATALOG_INDEX_LIMIT = 200;

export function useRuleCatalogIndex(): UseQueryResult<Map<string, RuleListItem>> {
  return useQuery({
    queryKey: riskQueryKey('rules', 'catalog-index'),
    queryFn: () => riskFetchJson<RuleListResponse>('/rules', { limit: RULE_CATALOG_INDEX_LIMIT }),
    select: (data: RuleListResponse) => new Map(data.items.map((r) => [r.rule_id, r])),
  });
}
