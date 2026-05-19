# Data Governance

The data-governance extension of Kagenti: ingests OTEL spans from Kagenti agents,
tools, and services, stores them verbatim in Postgres, and exposes typed retrieval
for a UI and future processors. v1 is span-shaped only — semantic concepts
(interactions, lineage, classifications) are deliberately deferred.

## Language

**Span**:
A single OTEL span row in the `spans` table, identified by the composite key
`(trace_id, span_id)`. Stored verbatim with payloads inline in `attributes`.

**Trace**:
The set of all **Spans** sharing a `trace_id`. Has no inherent root or boundary
beyond the shared id; "tree shape" is derived from `parent_id` links at query
time.
_Avoid_: "request", "session" — those imply semantics v1 does not assign.

**Listing root**:
The one **Span** chosen to represent a **Trace** in a UI listing row. Defined as
the trace's earliest **Real root** if one exists, otherwise its earliest
**Orphan span**. Computed at query time, not stored — see ADR-0001.
_Avoid_: "root span" alone (ambiguous between Real root and Listing root).

**Real root**:
A **Span** whose `parent_id IS NULL`. The OTEL-defined start of a trace.

**Orphan span**:
A **Span** whose `parent_id` is set but whose referenced
`(trace_id, parent_id)` does not exist in `spans` at query time. The parent may
have been dropped by the §3.1 blocklist, lost in transit, evicted by a future
retention policy, or simply not yet ingested. Orphan-ness is **eventually
consistent**: a span that is an orphan at T1 may cease to be one at T2 when its
parent arrives.

**In-window**:
A property of a **Span** in a query result, true iff its `started_at`
(trace-clock, OTLP-supplied) falls within the request's `(time_from, time_to)`.
False on out-of-window spans returned because they are **Listing roots** of
in-window traces. Carried as the `in_time_window` field on returned `Span`
objects; defaults to true when the caller supplied no window. Window basis is
trace-clock, not receiver-clock — so `observed_at` (when the receiver saw the
span) does not affect window membership.

**Listing root fallback**:
The rule by which a **Trace** with no **Real root** still gets a **Listing
root**: pick the earliest **Orphan span**. Lets the UI show every trace with
in-window activity, even when the OTEL root never arrived.

**Blocklist**:
The hardcoded set of span-name patterns (exact or `prefix*`) that the
`P-otel-receiver` drops at the OTLP socket before writing. See PROJECT.md §3.1.

**P-otel-receiver**:
The single v1 ingestion processor. Owns the OTLP socket (gRPC and HTTP),
applies the **Blocklist**, and writes accepted **Spans** verbatim to the
`spans` table. Stays semantically unaware — no classification, normalization,
correlation, or payload extraction.

**Retrieval API**:
The read-only Python library that consumers (UI backend, future processors)
call to read **Spans**. Exposes typed methods plus a SQL escape hatch.

**TraceListingEntry**:
One row of the recent-traces UI view — a derived display of a **Trace**,
anchored on its current **Listing root**. Rendered from a `GET /spans` call
with `root_only=true` (each returned `Span` is one trace's listing root).
Eventually consistent: the listing root, and therefore the row's display
fields, may change as late spans arrive. Identity is `trace_id`; everything
else is derived. The UI dedupes by `trace_id` across paginated responses to
collapse anchor flips into a single row.

## Relationships

- A **Trace** contains one or more **Spans**, all sharing its `trace_id`.
- A **Span** has at most one parent **Span** within the same **Trace** (or no
  parent, making it a **Real root**, or a missing parent, making it an
  **Orphan span**).
- A **Trace** has zero or more **Real roots** (typically one) and zero or more
  **Orphan spans**. It always has exactly one **Listing root** at any given
  moment, chosen by the **Listing root fallback** rule.
- A **TraceListingEntry** is a derived view of one **Trace**, anchored on its
  current **Listing root**.
- The **Retrieval API** is the only sanctioned read path over **Spans**; the UI
  backend composes its REST endpoints from it.

## Example dialogue

> **Dev:** "If a trace's `parent_id IS NULL` span hasn't arrived yet, does
> `GET /spans?root_only=true` skip the trace?"
> **Designer:** "No. The **Listing root fallback** picks the earliest **Orphan
> span** as the **Listing root** so the trace still shows up. The UI flags it
> by checking whether the returned listing root's `parent_id` is null
> (**Real root**) or not (orphan acting as listing root)."

> **Dev:** "What if the real root arrives later?"
> **Designer:** "Then the **Listing root** flips on the next query. The
> **TraceListingEntry**'s display fields update — that's the eventual
> consistency we accept in v1. See ADR-0001."

## Flagged ambiguities

- **"Root"** was used loosely to mean both `parent_id IS NULL` (OTEL sense) and
  "the span shown in the listing" (UI sense). Resolved: **Real root** vs
  **Listing root** — distinct terms.
- **"Trace"** vs **"TraceListingEntry"** — the OTEL set of spans vs the UI row
  derived from it. They share `trace_id` but the row's other fields are
  derived and eventually consistent; the trace itself is just the set.
