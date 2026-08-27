/**
 * Wire types for the `/risk/metrics/*` and `/risk/traces` endpoints consumed
 * by the risk dashboard (issue #169). Verbatim snake_case, matching
 * `data_governance/risk/api/metrics_routes.py` and `risk_routes.py`'s JSON
 * shapes exactly — not folded into `ui/src/types.ts`, whose header scopes
 * that file to the `/api/` tree, distinct from `/risk/`'s own resource tree
 * (see `risk-api/client.ts`).
 */

/** One FR-DAS-050a summary tile: `aggregate.TileCounts`. */
export interface TileCounts {
  total: number;
  risky: number;
  risky_pct: number;
}

/** The `rules_fired` tile has no `risky`/`risky_pct` — see `_summary_handler`. */
export interface RulesFiredCounts {
  total: number;
  critical: number;
}

/** `GET /risk/metrics/summary` response body. */
export interface RiskSummary {
  window: string;
  from: string;
  to: string;
  agents: TileCounts;
  users: TileCounts;
  workflows: TileCounts;
  evaluated_interactions: TileCounts;
  rules_fired: RulesFiredCounts;
  computed_at: string;
}

/** `GET /risk/metrics/risk-distribution` response body. */
export interface RiskDistributionResponse {
  window: string;
  distribution: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    none: number;
    total: number;
  };
  computed_at: string;
}

/** One row of `GET /risk/metrics/risk-by-category`'s `items`. */
export interface CategoryCount {
  category: string;
  count: number;
}

/** `GET /risk/metrics/risk-by-category` response body. */
export interface RiskByCategoryResponse {
  window: string;
  items: CategoryCount[];
}

/**
 * One row of `GET /risk/metrics/top-rules`'s `items`. `rule_name` and
 * `risk_level` are nullable — a rule id absent from the catalog is still
 * counted, with both fields `null` (`aggregate.get_top_rules`'s docstring).
 */
export interface TopRuleItem {
  rule_id: string;
  rule_name: string | null;
  count: number;
  trace_count: number;
  risk_level: string | null;
  risk_level_distribution: Record<string, number>;
}

/**
 * `GET /risk/metrics/top-rules` response body. No `next_cursor` — this is a
 * bounded top-N ranking, not a paginated list (implementation-notes-v3 §8.2's
 * documented exception for metrics endpoints).
 */
export interface TopRulesResponse {
  window: string;
  items: TopRuleItem[];
}

/** One row of `GET /risk/traces`'s `items` — `risk_routes.trace_risk_to_json`. */
export interface TraceRiskRecord {
  trace_risk_id: string;
  trace_id: string;
  version: number;
  computed_at: string;
  trace_risk_level: string;
  trace_enforcement_type: string | null;
  risk_compounding_mode: string;
  enforcement_aggregation_mode: string;
  interaction_count: number;
  policy_event_count: number;
  all_entity_ids: string[];
  triggered_rule_ids: string[];
  overall_confidence: number | null;
  contributing_interaction_risk_ids: string[];
}

/** `GET /risk/traces` response body — keyset-paginated, unlike the metrics endpoints. */
export interface TraceRiskListResponse {
  items: TraceRiskRecord[];
  next_cursor: string | null;
}
