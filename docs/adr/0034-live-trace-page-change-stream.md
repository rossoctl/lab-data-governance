---
status: accepted
---

# The trace page is live: a change stream of trace version tokens

A trace is derived while it is being read. Spans arrive, the interactions
processor re-derives, the lineage processor follows, and the reader of
`/ui/traces/{tid}/graph` sees none of it until they reload. The Execution
Flow, Interaction flow, Interaction diagram and Lineage views are all
whole-trace reads over the derived tables; the page fetched them once and
TanStack Query's 30-second `staleTime` with focus refetch off meant it never
fetched them again.

This ADR makes the open trace page follow its trace: automatically, with no
control to switch it off, and without the picture flickering or the reader's
own arrangement of it being lost.

## Decision

### One stream per open trace, carrying a version token

`GET /api/traces/{tid}/events` is a Server-Sent Events stream with one event
type, `change`, whose `id` and `data` are the trace's **Trace version** token.
The token (`retrieval.get_trace_version`) is a digest of the derived
high-water marks for that trace — highest `interaction_legs.seq`, counts of
`interactions` and `entity_spans`, highest `lineage_metadata.seq`, the lineage
status — and is compared for equality only. It says *that* the trace changed,
never *what*: every read the page makes is a whole-trace read, so "changed" is
already the full instruction.

The first event on every connection is the current token. That makes the
server stateless per stream and the browser's automatic reconnect correct
without reading `Last-Event-ID`: the client compares each token with the last
one it saw, and a change made while disconnected shows as a differing token.

### Wake by LISTEN, guarantee by backstop — the processors' own rule

A stream learns of a change the way the processors do (ADR-0015, ADR-0027):

- a **wake** from one process-wide `ChangeHub` holding a single `LISTEN`
  session on the derivation's existing notify channels (`dg_legs_inserted`,
  `dg_entity_ready`). Latency only: payload-less, global, lost if the session
  is down;
- a **backstop**: every stream re-reads its token at least every 3 seconds.
  This is the correctness guarantee — the lineage processor fires no notify,
  and a `LISTEN` session may be reconnecting.

A wake is first given 250 ms for the burst that caused it to land, so a trace
re-derived span by span (the sidecar algorithm, ADR-0030, re-inserts the whole
trace per span) costs a few token reads per second rather than one per span.
On either signal the stream emits `change` only if the token differs; a quiet
backstop emits an SSE comment, which is the keepalive. No new trigger, no
migration, no payload on any notification: ADR-0027's "reliability lives in
the drain, not the notify" holds here with the token read as the drain.

`db.listen` now takes one channel or a sequence, so the hub registers both
on one session (ADR-0015's connection shape, unchanged). The hub is started
from the app's lifespan with the `DATABASE_URL` the API already has; built
without a DSN (tests, or a deployment that cannot reach a `LISTEN` session) it
never listens and the streams run on the backstop alone.

### The page subscribes once; the views keep reading as before

`useTraceLive(traceId)` is mounted once by `TraceDetailPage`. On a `change`
whose token differs from the last seen it invalidates the trace's query keys
(`trace`, `interactions`, `entities`, `data-lineage`, `lineage-summary`,
`lineage-graph`); TanStack refetches the mounted ones. The views underneath
are untouched and know nothing about liveness — it is a property of the page
being open, not of any tab. The first token also invalidates once, closing
the window between the page's first reads and the stream's first event.

Two modes, decided by the browser: `stream` normally, and `poll` (invalidate
every 2 s) while the stream is in error, until it comes back. A dropped
stream the browser retries by itself; one it has *closed* it never does —
the EventSource specification closes the source on any non-200 reply, and a
gateway answers 503 for the seconds in which the API has no ready endpoint —
so a closed source is reopened on the poll cadence until its first token
arrives. A `Live` / `Polling` label by the title is the only visible trace
of it. Without an
`EventSource` at all — jsdom, not a supported browser — the hook does nothing,
so the page behaves as it did before and no test that counts fetches sees a
timer it did not start.

The **span tree is not live**. It is keyset-paginated with its own
expand/reveal state and a Refresh button; re-fetching its pages underneath an
open reveal is a different problem from re-reading a whole-trace derivation,
and is left for its own decision.

### A background read that fails keeps the picture

A live page re-reads often, and a transient failure must not replace a good
picture with a failure alert. The gates that used `isError` on the trace,
entities/interactions and lineage reads now use `isLoadingError`: a read that
never produced an answer is reported as failed; one that has an answer and
could not refresh it keeps showing the answer.

### The graph merges and moves instead of rebuilding

`EntityGraph` pushed every model with `fromModel(model, false)`, which
destroys every element and recreates it — a full repaint from a reset viewport
on every push. Tolerable when a push was a selection click; not when it is
every derived write. The push is now a **merge**: an element whose id is in
the new model is updated in place and keeps its DOM, pan and zoom; one that is
not is removed (a node from another trace, an interaction a re-derivation
dropped). The node and edge renderers became MobX observers to go with it —
their `data` now changes on an element rather than arriving on a fresh one.

A node whose layered cell changed **moves** there instead of jumping
(`ui/src/lib/motion.ts`), and so does the one bendpoint of an existing edge
whose route changed — a bendpoint is stored geometry, so without that it would
jump to the new route while the nodes at its ends were still travelling. Each
thing that moves chases its target with a critically damped spring rather than
playing a fixed-length tween: a push that lands while things are still moving
(the normal case on a live page) just gives them new targets, keeping their
position and speed, where a tween would have to finish (a jump) or restart
from rest (a stop) — the first version did the former, and it showed. One
frame's writes run in one MobX action, so the observers redraw once per frame,
and one late frame advances the motion by at most two frames' worth, so a busy
moment slows the motion down instead of skipping it.
The reader wins: a node found somewhere other than where the last frame left
it is being dragged and is let go; a node still moving at the next push is not
harvested as "dragged there". `prefers-reduced-motion` gets the destination at
once. Identical data from a refetch keeps its identity through structural
sharing, so a change event that changed nothing visible pushes nothing.

### One reading of entities and interactions

A change event re-reads entities and interactions as two requests, which land
at different moments. Drawn as they landed, the graph passed through a state
the trace was never in: a new entity whose call was not read yet has no edge,
so the layout parked it in the unconnected column, and it moved to its real
cell a moment later. The views that draw the two together (the graph tabs and
the flow tables) now take them from `useEntitiesAndInteractions`, which keeps
the previous pair while either read is in flight and swaps both in together.
Each read is a whole-trace read in its own transaction and the processor
commits a trace's derivation in one transaction per span, so the pair can
still straddle a commit in a narrow window; the next change event corrects it.

## Consequences

- Lag from a derived write to the picture is the coalescing grace plus one
  read, measured well under a second with the `LISTEN` wake; at most one
  backstop (3 s) without it. The lineage tab, whose writes fire no notify,
  runs at the backstop.
- Every open trace page holds one SSE connection and one subscription in the
  hub. A proxy in front of the API must pass `text/event-stream` through
  unbuffered (the stream sets `X-Accel-Buffering: no`); one that does not
  leaves the page on its polling fallback, visibly.
- The token read is three small indexed queries per wake per open page. A
  burst on another trace wakes every stream (the notify is global) and costs
  each one a read that finds no change — bounded by the coalescing grace.
- Reset View still snaps: it is the reader's own act and reads as one.
- Observed in a local rig (receiver, processors, API, Postgres; no cluster):
  a 310-span trace replayed at five spans per second drew itself in as the
  derivation ran, a dragged node stayed where it was put throughout, the API
  process was stopped and restarted under the page (label flipped to
  `Polling` and back to `Live`, picture never blanked), and the sidecar
  algorithm's per-span re-derivation re-labelled a node in place.
- Observed on the `rossoctl` cluster behind the Istio gateway: the stream
  passes unbuffered (first token at once, keepalives every 3 s); a real
  travel-advisor turn drew its graph in while the agents ran; deleting the
  API pod under the open page gave the 503-closes-the-source case above,
  which the rig could not produce (a stopped process refuses the connection,
  which the browser does retry) and which the reopen now covers.
