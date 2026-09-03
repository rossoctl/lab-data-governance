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

/**
 * Rule catalog wire types (issue #171), matching
 * `data_governance/risk/rules/catalog.py`'s `_flatten_rule` §6.5 serving
 * shape exactly. Note this is a flat catalog entry, not a PRD §7.7 literal
 * "conditions" array — a rule's match criteria are structural fields whose
 * presence is the predicate (`event_type`, `data_items`,
 * `data_destinations`), per `catalog.py`'s module docstring. The rules UI
 * renders these as condition-type/value rows to match the mockup's
 * Conditions table without inventing a `conditions` field the API doesn't
 * return.
 */

/** One `GET /risk/rules/{rule_id}` `rule_sources` entry — a citation, not a URL. */
export interface RuleSource {
  document_name: string;
  version: string;
  section: string;
  'article/clause': string;
}

/** One `data_items` match-criteria entry. Every field is optional — a rule's
 * on-disk entry only has the criteria that apply to it. */
export interface RuleDataItem {
  regulatory_tags?: string[];
  classification_level?: string;
  [key: string]: unknown;
}

/** One `data_destinations` match-criteria entry. */
export interface RuleDataDestination {
  data_destination_categories?: string[];
  data_destination_trust_level?: string;
  [key: string]: unknown;
}

/**
 * One rule, the shape both `GET /risk/rules`'s `items` and
 * `GET /risk/rules/{rule_id}` return (the detail endpoint returns one of
 * these, not a wrapped envelope — `_rule_detail_handler` calls
 * `http.json_ok(rule)` directly). `rule_name`, `risk_level`, `enforcement`,
 * and `explanation` are nullable: `_flatten_rule` reads them from an
 * optional `rule_decision` object that some catalog entries may omit.
 */
export interface RuleListItem {
  rule_id: string;
  rule_name: string | null;
  categories: string[];
  risk_level: string | null;
  enforcement: string | null;
  explanation: string | null;
  event_type: string | null;
  data_items: RuleDataItem[];
  data_destinations: RuleDataDestination[];
  allowed_actions: string[];
  rule_sources: RuleSource[];
}

/** `GET /risk/rules` response body — cursor/limit paginated per §8.1. */
export interface RuleListResponse {
  items: RuleListItem[];
  next_cursor: string | null;
}

/** One `GET /risk/rules/categories` `items` entry — FR-DAS-061. */
export interface RuleCategoryCount {
  category: string;
  rule_count: number;
}

/** `GET /risk/rules/categories` response body. */
export interface RuleCategoriesResponse {
  items: RuleCategoryCount[];
}
