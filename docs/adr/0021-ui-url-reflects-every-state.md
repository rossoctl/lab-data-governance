# The browser URL is the single source of truth for every UI state

The React SPA (ADR-0019) served under `/ui/` originally kept two kinds of state
only in React `useState`: the recent-traces list's **time window** + its
**hide-missing-parent** filter, and the trace-detail **active tab** (span tree
vs interaction flow) plus the **selected row** in each view. The URL captured
only the trace id — `/ui/traces/{id}` regardless of which tab or selection was
on screen. Reload, bookmark, and browser-back all discarded that state.

This ADR makes the **URL the single source of truth for UI state**: every
distinct on-screen state has its own URL, so reload / bookmark / back / forward
restore exactly what the user was looking at. It layers a *client-side* route
structure on top of the *server-side* `/api/` vs `/ui/` namespacing that
ADR-0017 fixed; it does not change any `/api/` route or the backend's `/ui/`
catch-all (which already serves `index.html` at any depth, so deep links
resolve client-side).

## The `/ui/` URL contract

| URL | UI state |
|-----|----------|
| `/ui/` | → redirect to `/ui/traces` |
| `/ui/traces` | recent-traces list, default window `1h` |
| `/ui/traces?window=<key>` | list, window ∈ `15m 1h 6h 24h all` (the internal keys) |
| `/ui/traces?hideOrphans=1` | list with missing-parent traces filtered out |
| `/ui/traces/{id}` | → redirect to `/ui/traces/{id}/spans` (canonical) |
| `/ui/traces/{id}/spans` | span tree (the default tab) |
| `/ui/traces/{id}/spans?sel={spanId}` | span tree, that span selected + revealed |
| `/ui/traces/{id}/flow` | interaction flow, default presentation `tree` |
| `/ui/traces/{id}/flow?iid={interactionId}` | flow, that interaction row selected |
| `/ui/traces/{id}/flow?eid={entityId}` | flow, that entity row selected |
| `/ui/traces/{id}/flow?legs=<key>` | flow table presentation ∈ `tree flat` (ADR-0029) |
| `/ui/traces/{id}/diagram` | interaction sequence diagram (ADR-0029) |
| `/ui/traces/{id}/graph` | Execution Flow topology graph (ADR-0029) |
| `/ui/traces/{id}/lineage` | Execution Flow graph, lineage-highlighted (ADR-0029) |
| `/ui/traces/{id}/lineage?src=<naturalKey>` | the data source being traced (ADR-0029) |
| `/ui/traces/{id}/flow?legs=diagram\|graph\|lineage` | → redirect to the matching segment above, other params carried (ADR-0029) |
| anything else | → redirect to `/ui/traces` |

- **Tab = path segment, filter/selection = query param.** The tab is a
  first-class sub-resource of a trace (its own path segment, `/spans` | `/flow`),
  matching how `kagenti/ui-v2` models sub-views (`/sandbox/graph`,
  `/sandbox/files/...`). The window, orphan filter, and row selection are
  refinements *of* a view, so they are query params (matching ui-v2's
  `?session=`, `?path=`). This mirrors the repo's own precedent rather than
  inventing a scheme.
- **`spans`, not `tree`.** The URL word for the span-tree tab is `spans` (the
  resource it shows), even though the component's internal `ViewKey` is `tree`.
  The page maps between them; the URL term is chosen for the reader of the URL.
- **Bare paths canonicalise by redirect.** `/ui/` → `/ui/traces` and
  `/ui/traces/{id}` → `/ui/traces/{id}/spans`, so each state has exactly *one*
  canonical URL rather than a bare-vs-explicit pair.
- **Defaults are omitted, not written.** `?window=1h` and `?hideOrphans=0` are
  never emitted — the default is the absence of the param — so a fresh list is
  the clean `/ui/traces`.
- **`iid` and `eid` are mutually exclusive.** The flow view allows one selected
  row across both tables, so writing one selection param clears the other.

## Why

- **Reload/bookmark/back is the whole point.** An observability UI is shared by
  pasting a link ("look at this trace's flow, this interaction"). If the link
  only carries the trace id, the recipient lands on a different state than the
  sender saw. Encoding tab + selection in the URL makes a link reproduce a view.
- **One canonical URL per state keeps history sane.** Redirecting bare paths to
  their canonical form means back/forward walk real states, not redirect stubs,
  and two links to "the same thing" are string-equal.
- **Selection restore reuses the existing reveal machinery.** The span-tree
  already exposes an imperative `reveal(spanIds)` that loads + expands ancestors,
  selects, and scrolls (built for the flow → tree "jump to span" jump). Driving
  it from `?sel` unifies deep-link restore with that in-app jump on one code
  path, rather than adding a parallel restore mechanism.

## Considered alternatives

- **Keep the list at the `/ui/` root, tab-only in the URL.** Rejected: the list
  is a resource collection (recent traces); naming it `/ui/traces` makes the
  collection ↔ singular relationship (`/ui/traces` → `/ui/traces/{id}/...`)
  legible and matches the `/api/traces` shape one layer down. The `/ui/` root
  becomes a pure redirect.
- **Encode the tab as a query param (`/ui/traces/{id}?view=flow`).** Rejected:
  the tab selects *which sub-resource* of the trace is shown, which is a path
  concern, not a refinement of one view. Query params are reserved for
  refinements (window, filter, selection).
- **Persist the multi-set highlight pins in the URL too.** Deferred: pins are
  transient, multi-set, and can carry many span ids; they are not part of "which
  view + which row" and would bloat the URL. Left in memory for now.

## Consequences

- The masthead brand link and the `*` fallback route point at `/ui/traces` (the
  canonical list), not `/`.
- `RecentTracesPage` reads window + orphan filter from `useSearchParams` instead
  of `useState`; `TraceDetailPage` derives the active view from the `:view`
  route param instead of `useState`, and mirrors the tree/flow selection into
  `?sel` / `?iid` / `?eid`. The `SpanTree` and `FlowTables` components stay
  URL-agnostic — the page owns the URL and drives them through their existing
  props (`onSelect`, `reveal`, and new `initialSelection` / `onSelectionChange`
  on the flow view).
- Switching tabs drops the query string (a `?sel` from the tree is meaningless
  in the flow view, and vice versa).
- No backend change: the Starlette `/ui/{path:path}` → `index.html` catch-all
  already serves the SPA shell for every `/ui/*` depth, so the deeper deep links
  (`/ui/traces/{id}/flow`, `/ui/traces?window=all`) reload without a 404.
