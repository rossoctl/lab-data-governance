/**
 * Wire shapes for the `/api/` resource tree (ADR-0017 namespacing, ADR-0018
 * resource tree). These mirror the backend's JSON exactly — `Span` is the
 * full-row dataclass (ADR-0006), datetimes arrive as ISO-8601 strings.
 */

/** Per-trace counts ridealong on the listing (ADR-0018 `counts`). */
export interface TraceCounts {
  total: number;
  in_window: number;
  error_count: number;
}

/** A single row from the `spans` table (ADR-0006: full row, one shape). */
export interface Span {
  seq: number;
  trace_id: string;
  span_id: string;
  parent_id: string | null;
  name: string;
  started_at: string;
  attributes: Record<string, unknown>;
  observed_at: string;
  arrival_seq: number;
  in_time_window: boolean;
  service_name: string | null;
  kind: string | null;
  error: boolean | null;
  status_message: string | null;
  events: Array<Record<string, unknown>> | null;
  links: Array<Record<string, unknown>> | null;
  ended_at: string | null;
  otlp: Record<string, unknown> | null;
  scope: Record<string, unknown> | null;
  resource_attributes: Record<string, unknown> | null;
}

/**
 * A trace-shaped listing element (CONTEXT.md **TraceListingEntry**): identity
 * `trace_id`, the anchor **Listing root** nested, per-trace counts inline. The
 * element of `GET /api/traces` and the body of `GET /api/traces/{tid}`.
 */
export interface TraceListingEntry {
  trace_id: string;
  listing_root: Span;
  counts: TraceCounts | null;
  in_time_window: boolean;
}

/** Span-evidence row for an interaction/entity `/spans` sub-resource (ADR-0013). */
export interface SpanEvidence {
  span_id: string;
  /** interaction: anchor|connector|info; entity: discovered_via|identified_via. */
  role: string;
  parent_id: string | null;
  kind: string | null;
  service_name: string | null;
}

/** A payload row addressed by content hash (`GET /api/payloads/{hash}`). */
export interface Payload {
  content_hash: string;
  content_kind: string;
  content: unknown;
  byte_size: number;
}

// Entity and Interaction wire shapes live in ./lib/flow (the pure helpers key
// on them); re-export so consumers import all wire types from one module.
export type { Entity, Interaction } from './lib/flow';
