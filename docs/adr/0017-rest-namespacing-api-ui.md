# REST surface namespaced: JSON under `/api/`, pages and assets under `/ui/`

PR #87 deliberately stripped the `/proto/` prefix from the route scheme to
get clean, unprefixed REST routes (`/traces/{id}`, `/payloads/{hash}`, the
interaction/entity sub-resources). This ADR **reverses that direction**: all
JSON resources move under `/api/` and all HTML pages join the JS assets under
`/ui/`, with bare `/` issuing a 302 to `/ui/`. `/healthz` stays at the root as
an infra probe.

We do this because collapsing `GET /spans` into a trace/span resource tree
(ADR-0018) put a JSON `GET /traces/{tid}` singular on the *same path* as the
existing HTML *page* `GET /traces/{trace_id}` (which serves `trace_tree.html`;
its JS reads `trace_id` from `window.location.pathname`). One path cannot be
both a JSON resource and an HTML shell. Prefixing separates them by intent
rather than by content negotiation.

## Why

- **The page-vs-resource collision is real, not stylistic.** Without the
  split, `/traces/{tid}` would have to branch on `Accept` headers or a query
  flag to decide page vs JSON — exactly the two-shapes-one-route smell
  ADR-0006 rejected, one layer up.
- **Assets were already under `/ui/`.** The JS served from `/ui/{asset}`
  established the prefix; moving the HTML shells to `/ui/` and `/ui/traces/{tid}`
  makes the UI namespace whole instead of half-applied.
- **`/api/` is a stable, greppable boundary.** A reader (or a reverse proxy,
  or a future auth layer) can reason about "the JSON API" as one prefix.

## Considered alternatives

- **Keep unprefixed routes, disambiguate `/traces/{tid}` by content
  negotiation.** Rejected — see above; it recreates the rejected
  two-shapes-one-route pattern.
- **Move only JSON to `/api/`, leave HTML pages at the root.** The `/api/`
  prefix alone removes the collision, so this was viable. Rejected for
  coherence: pages at `/traces/{tid}` sitting beside JSON at
  `/api/traces/{tid}` reads as an overlap; a clean three-way split
  (`/api` JSON, `/ui` pages+assets, `/` redirect) is easier to hold in
  the head.

## Consequences

- **Reverses PR #87's unprefixed-routes goal — deliberately.** This ADR
  exists primarily so a future reader who finds the `/proto/`-removal commit
  does not "restore" clean unprefixed routes and reintroduce the page/JSON
  collision. The de-`/proto/` intent (drop *semantic* framing from paths)
  survives; `/api/` is a *layer* boundary, not semantic framing.
- **`trace_tree.html` must parse `trace_id` from `/ui/traces/{tid}`.** The
  in-page `window.location.pathname` parse and index.html's navigation links
  change to the `/ui/` prefix.
- **Every JSON fetch in the UI re-prefixes to `/api/`** (recent-traces,
  cold-open, tree expand, single-span refresh, payload, interactions,
  entities). This is the same edit surface the `GET /spans` retirement
  already touches (ADR-0018), so the two land together.
- **Path-param names shortened alongside** (`trace_id`→`tid`, `span_id`→`sid`,
  `interaction_id`→`iid`, `entity_id`→`eid`, `content_hash`→`hash`). These are
  internal Starlette identifiers with no URL-contract effect; the rename is a
  readability change bundled into the same reshaping churn, applied uniformly
  to avoid a mixed convention.
