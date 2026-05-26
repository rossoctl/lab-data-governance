# Retrieval `Span` is the full row, not a curated subset

The retrieval API's `Span` dataclass started as a curated projection of the
`spans` table — listing-friendly columns only. The trace-tree detail panel
needs every column the row carries (timing, provenance, OTLP envelope,
scope, resource attributes), and the natural question is whether to widen
`Span`, add a `full=true` flag, or split into a separate "single-span"
endpoint.

We chose to **widen `Span` to be the full row.** The retrieval API exposes
one shape; every `/spans` response carries every column. Listing rows pay a
small payload cost so that detail rendering, refresh, and any future
read-side consumer use the same object.

## Why

- **One shape, one contract.** A `Span` returned by `/spans?root_only=true`
  is the same object as one returned by `/spans?trace_id=T&span_id=S`.
  Consumers do not branch on response shape; the dataclass docstring
  documents one set of fields.
- **The detail-panel use case is real, not hypothetical.** Issue #14
  introduced the trace tree with a span-detail side panel. The panel
  already needs `attributes`, `events`, `links`, `kind`, `error`,
  `status_message`, `service_name` — all of which were promoted to
  `Span` for that slice. Continuing the same logic for `ended_at`,
  `observed_at`, `arrival_seq`, `otlp`, `scope`, and `resource_attributes`
  is the consistent next step rather than a new pattern.
- **The receiver already stores everything.** Per the **P-otel-receiver**
  language entry, OTLP fields are preserved verbatim across `attributes`,
  `events`, `links`, `scope`, `resource_attributes`, and the `otlp`
  envelope. The retrieval API was the only layer dropping fields; the
  database was not. Widening the dataclass aligns the read surface with
  the storage shape.
- **No schema change.** All widened fields already exist on the row. This
  is purely a dataclass + SELECT + serialization change; the source of
  truth (the schema) is unchanged.

## Considered alternatives

- **Add `full=true` query flag.** Rejected: two response shapes for one
  endpoint, doubled documentation and test surface, and every consumer
  must decide which shape it wants. The "lean listing" benefit is small
  — a TraceListingEntry row is dominated by `attributes` size, which is
  already returned today.
- **Separate `/spans/<trace_id>/<span_id>/full` endpoint.** Rejected:
  splits the read contract along an axis (single-span vs. listing) that
  doesn't actually correspond to a different query — the existing
  `?trace_id=T&span_id=S` form already returns a single span. A second
  route would only serve to carry a different shape, recreating the
  problem from option 1.
- **Leave `Span` curated, render the panel from a separate "raw row"
  endpoint.** Rejected: bypasses the typed retrieval API's no-SQL-escape-
  hatch posture (ADR-0005). The retrieval API is the only sanctioned
  read path over **Spans**; a second path that returned more fields
  would fork that posture.

## Consequences

- **Listing payloads grow.** A `/spans?root_only=true` page now carries
  `otlp`, `scope`, and `resource_attributes` per row. At v1 scale this
  is bytes-per-row, not kilobytes, and well within the 50-row default
  page. If a future caller needs a leaner listing it can request fewer
  fields explicitly — but no such caller exists today.
- **The dataclass docstring is the field index.** Adding a column to the
  `spans` table now also means adding it to `Span`. The receiver and the
  retrieval API are coupled at the dataclass level — intentional, given
  ADR-0005's "domain functions return domain objects" rule.
- **`Span` is no longer listing-shaped.** Some fields (`otlp`,
  `arrival_seq`, `observed_at`) are diagnostic-only — they exist for
  debugging finalization or ingestion order, not for the recent-traces
  UI. A future processor may still want only listing fields; that's a
  caller-side projection, not an API split.
- **Eventual consistency carries through.** `ended_at`, `error`,
  `status_message`, and `seq` are mutable on **Finalization** (ADR-0004).
  The detail panel renders whatever is on the `Span` it was given;
  freshness is opt-in via a refresh button that re-fetches the single
  span.
