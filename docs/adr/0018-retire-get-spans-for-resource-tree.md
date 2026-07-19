# Retire `GET /spans` in favour of a trace/span resource tree

`GET /spans` (the issue #4 tracer bullet) was a single pass-through over the
`get_spans` retrieval function: one route serving five distinct query shapes
selected by query-parameter combinations — recent-traces listing
(`root_only=true&time_from&time_to`), one trace's listing root
(`root_only=true&trace_id`), keyset-paginated children (`trace_id&parent_id`),
a single span (`trace_id&span_id`), and all-spans-in-a-trace. We **remove the
`/spans` route entirely** and redistribute those shapes across a
resource-oriented tree (paths shown post-ADR-0017 namespacing):

- `GET /api/traces` — recent-traces feed → `{traces:[TraceListingEntry]}`
- `GET /api/traces/{tid}` — one **TraceListingEntry** (cold-open seed)
- `GET /api/traces/{tid}/spans` — whole trace, flat, paginated
- `GET /api/traces/{tid}/spans/{sid}` — one **Span**
- `GET /api/traces/{tid}/spans/{sid}/children` — that span's direct children,
  keyset-paginated

The **library** `get_spans` is unchanged — its `root_only`, `parent_id`,
`cursor`, and parameter-compatibility raises all survive. Only the HTTP surface
is reshaped; the REST handlers still call `get_spans` underneath.

## Why

- **One route, five behaviours was opaque.** Which parameter combinations were
  legal lived in `get_spans`'s compatibility raises (`root_only` incompatible
  with `parent_id`/`span_id`, `parent_id` requires `trace_id`), not in the URL.
  The resource tree makes each legal shape its own addressable path.
- **The recent-traces feed is trace-shaped, not span-shaped.** The
  `root_only=true` result was really a list of **TraceListingEntry** (identity
  `trace_id`, anchor span nested, per-trace **Trace counts**) but was serialised
  as `{spans:[...], counts:{trace_id:...}}` — a span list with a sidecar map the
  client had to zip. `GET /api/traces` now returns
  `{traces:[{trace_id, listing_root, counts, in_time_window}]}`; the route name
  and the payload agree.
- **Consistency with the PR #87 tree.** `/api/traces/{tid}/interactions`,
  `/entities`, and their `/spans` sub-resources already existed. The span reads
  now nest under the same `{tid}` parent instead of living at a sibling
  top-level `/spans`.

## Considered alternatives

- **Children as `?parent_id=` on `GET /api/traces/{tid}/spans`.** Viable — the
  whole-trace collection filtered by parent. Rejected in favour of a
  `.../spans/{sid}/children` sub-resource so the parent→child relationship is
  addressable rather than a filter flag; the whole-trace collection then has a
  single unambiguous meaning (every span in the trace).
- **Fold single-span into a filtered collection (`.../spans?span_id=`).**
  Rejected: "one span" should be a singular resource (`.../spans/{sid}`), not a
  one-element filtered list the caller unwraps.
- **Keep `GET /spans` for the span-level reads, add `/traces` only for
  listings.** Rejected: leaves the opaque multi-shape route alive for the two
  span reads; the point was to retire it.

## Consequences

- **`get_spans`'s `root_only` / `parent_id` paths keep their only callers.**
  The retrieval function's parameter surface is now reachable *only* through
  the new REST handlers, not a generic `/spans` pass-through. The library
  contract (and ADR-0001's keyset-cursor listing semantics) is untouched.
- **`GET /api/traces/{tid}/spans` ships without a current caller.** The tree UI
  seeds from the **Listing root** and expands children-by-parent, so it never
  fetches the whole trace flat. The collection route is included anyway as the
  obvious root of the span resource tree and the natural home for a future
  export / API consumer; its absence would be the surprise.
- **Single-span-by-id becomes a distinct route — but not a distinct shape.**
  ADR-0006 rejected a *separate single-span endpoint* on the grounds that it
  would only exist to carry a *different shape*. That objection does not apply
  here: `GET /api/traces/{tid}/spans/{sid}` returns the same full-row `Span`
  object as a collection element. ADR-0006's "one shape, one contract" holds;
  this ADR only changes addressing, not the object.
- **Collection and singular are symmetric.** `GET /api/traces/{tid}` returns the
  identical `TraceListingEntry` shape (including **Trace counts**) as one element
  of `GET /api/traces` — the cold-open caller reads `.listing_root` and ignores
  the rest, but the shape is honest for future detail-header use.
