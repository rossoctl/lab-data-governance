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

/**
 * One **Finding** (CONTEXT.md): a sensitive item the NER model detected in a
 * **Payload**'s Classifiable text — a `(start, end)` region into that text, its
 * detected type (`entity_type`, an NER tag like `SSN`/`EMAIL` — NOT an
 * **Entity**, which is the interaction participant), the flagged `text` region,
 * and the sensitivity attributes derived for it. The document-level verdict is
 * aggregated up from the finding set. The verbatim JSONB shape P-classification
 * writes (issue #78's real path; the tracer-bullet stub emits `[]`).
 */
export interface Finding {
  /** The detected NER tag (`SSN`, `PN`, `EMAIL`, …). A finding's type is an NER
   *  tag, never an **Entity** (CONTEXT.md flagged ambiguity). */
  entity_type: string;
  /** Char offset of the flagged region's start into the Classifiable text. */
  start: number;
  /** Char offset of the flagged region's end into the Classifiable text. */
  end: number;
  /** The flagged text region (the substring `[start, end)` of the text). */
  text: string;
  sensitivity_level?: string;
  regulatory_tags?: string[];
  identifier_type?: string;
}

/**
 * The **Classification** verdict over one **Payload** (CONTEXT.md): the
 * document-level `sensitivity_level`, the regulatory tags it carries, whether
 * it holds an identity bundle, and the set of **Findings** within its body
 * text. Inlined nullable on `GET /api/payloads/{hash}` (ADR-0024): `null` while
 * the payload exists but P-classification has not yet run (the
 * eventual-consistency window), distinct from a real `PUBLIC` / zero-Findings
 * verdict.
 */
export interface Classification {
  sensitivity_level: 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL' | 'RESTRICTED';
  regulatory_tags: string[];
  contains_identity_bundle: boolean;
  is_personalized: boolean;
  primary_domain: string | null;
  findings: Finding[];
  model_version: number;
}

/** A payload row addressed by content hash (`GET /api/payloads/{hash}`). */
export interface Payload {
  content_hash: string;
  content_kind: string;
  content: unknown;
  byte_size: number;
  /** The inlined **Classification** verdict (ADR-0024); `null` during the
   *  eventual-consistency window before P-classification has processed it. */
  classification: Classification | null;
}

/**
 * The **Data lineage metadata** triple for one **Interaction leg** (ADR-0027):
 * where the payload's data came from and what happened to it on the way.
 *
 * 1. `data_sources` — the origins, each an **Entity**'s natural key.
 * 2. `source_transformations` — `data_source → transformations`. A *list* on the
 *    wire because JSON has no set; its order is insignificant. A source in
 *    `data_sources` need not appear here (no transformations recorded for it).
 * 3. `entities` — the **set** of entities the data passed through. An array on the
 *    wire only because JSON has no set type: it is **unordered**, and nothing may
 *    read flow order out of element position. The spec defers ordering to a future
 *    trace-derived API; the backend sorts it purely so a re-derivation is
 *    byte-identical. An origin's set is legitimately empty.
 *
 * `seq` is the row's own derivation cursor. An *empty* triple is a real derived
 * value (the payload originates here) — distinct from the absent row, which the
 * wire spells as a `null` `lineage` (see {@link DataLineageLeg}).
 */
export interface DataLineage {
  data_sources: string[];
  source_transformations: Record<string, string[]>;
  entities: string[];
  seq: number;
}

/**
 * One **Interaction leg** of a trace with its nullable lineage, the element of
 * `GET /api/traces/{tid}/data-lineage`.
 *
 * The leg key is `(interaction_id, leg_type)` (ADR-0027 D5) — content-addressed
 * `payload_hash` is a fact about the row, NOT the key: identical bytes at
 * different positions carry completely different lineage. `lineage` is `null` in
 * the eventual-consistency window before P-data-lineage has derived this leg,
 * mirroring the nullable `classification` of `GET /api/payloads/{hash}`
 * (ADR-0024).
 */
export interface DataLineageLeg {
  interaction_id: string;
  leg_type: 'request' | 'response';
  payload_hash: string | null;
  lineage: DataLineage | null;
}

/**
 * `(interaction_id, leg_type)` → that leg's nullable lineage, the shape
 * `useDataLineage` reduces the trace-scoped response to. Keys are
 * `` `${interaction_id}:${leg_type}` `` (see `legLineageKey`). A *present* key
 * with a `null` value is "derived-nothing-yet"; an *absent* key means the leg
 * carries no lineage row at all (e.g. the not-yet-migrated empty response) —
 * both render as "not yet computed", but the distinction is preserved.
 */
export type DataLineageByLeg = Map<string, DataLineage | null>;

/**
 * What the flow view can currently say about ONE leg's lineage — the prop
 * `DataLineageView` renders, and the reason it is a union rather than
 * `DataLineage | null`.
 *
 * Three facts a governance reader must be able to tell apart, because each
 * demands a different action:
 *
 * - `'derived'` — P-data-lineage produced this triple. It is the answer, *including*
 *   when the triple is empty (the payload originates here, ADR-0027 D3).
 * - `'pending'` — the read succeeded but this leg has no lineage yet (the
 *   eventual-consistency window, or a not-yet-migrated DB). *Wait.*
 * - `'error'` — the read itself failed, so nothing is known. *Retry.*
 *
 * A nullable triple could only express two of those, which is how a failed fetch
 * came to render as "not yet computed" — telling a reader to wait for an answer
 * that was never going to arrive. Making the third state a distinct arm makes
 * that conflation unrepresentable rather than merely discouraged: ADR-0027's rule
 * that "not yet computed" must never look like a derived answer applies with
 * equal force to "we failed to ask".
 */
export type LineageState =
  | { kind: 'derived'; lineage: DataLineage }
  | { kind: 'pending' }
  | { kind: 'error' };

/**
 * Whether a trace's derived lineage covers the whole trace (ADR-0027 D6, issue
 * #120), as served on the `GET /api/traces/{tid}/data-lineage` envelope.
 *
 * - `'complete'` — every leg had a payload; the sources listed are the full set.
 * - `'partial'` — derivation stopped at the first leg with an absent payload, so
 *   the legs carrying lineage are a **prefix**.
 * - `null` — not derived yet (or the status migration has not run). *Unknown*,
 *   which must never be rendered as `complete`: a governance reader taking a
 *   truncated prefix for the full source set is the failure this flag prevents.
 */
export type LineageStatus = 'complete' | 'partial' | null;

/**
 * The whole `GET /api/traces/{tid}/data-lineage` response, reduced to what the
 * flow view reads: the per-leg lookup plus the trace's own coverage.
 *
 * One hook, one fetch, both facts — the status is trace-level and the per-leg map
 * is derived from the same response, so splitting them across two hooks would
 * mean two reads of one resource.
 *
 * `stoppedAtSeq` is the leg `seq` where derivation stopped; non-null exactly when
 * `status` is `'partial'` (the server's CHECK constraint guarantees the pairing).
 */
export interface TraceDataLineage {
  byLeg: DataLineageByLeg;
  status: LineageStatus;
  stoppedAtSeq: number | null;
}

// Entity, Interaction and its InteractionLeg wire shapes live in ./lib/flow (the
// pure helpers key on them); re-export so consumers import all wire types from
// one module. `InteractionLeg` is part of that contract too — since ADR-0025 the
// leg is the grain timing, payload, error and `seq` live at, so callers building
// an interaction cannot avoid naming it.
export type { Entity, Interaction, InteractionLeg } from './lib/flow';
