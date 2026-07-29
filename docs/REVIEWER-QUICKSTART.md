# Reviewer quickstart — see two-span lineage working in ~15 minutes

For Igor and Micha. Goal: get one real agent turn captured by the sidecar, derived
by the `sidecar` interactions algorithm, and rendered in the DG UI — then know what
you are looking at, including the things that look wrong but are not.

Prerequisites: a kind cluster with kagenti installed, `kubectl`, and a container
runtime. Everything below is by pointer — nothing here is duplicated from the
operational docs it references.

## 0 · What this is, in four sentences

The AuthBridge lineage sidecar emits **two spans per HTTP exchange** — one when it
sees the request, one when it sees the response — paired by `lineage.exchange.id`.
The producer emits **facts only**; every judgment about what those facts mean lives
in the consumer. The P-interactions processor derives them with
`INTERACTIONS_ALGORITHM=sidecar` (ADR-0028), a peer of `streaming` and `graph`,
writing the ADR-0025 legs schema. Apps are **not modified**: a deploy-time,
propagate-only OTel shim makes `traceparent` flow through the app, exporting
nothing.

The wire is fixed by [`sidecar-wire-contract.md`](sidecar-wire-contract.md) (v1.1) —
that document is the law this branch implements.

## 1 · Deploy data-governance (~4 min)

```sh
./deploy/build-and-load.sh          # builds + loads the image (see its header for podman notes)
kubectl apply -f deploy/k8s/
kubectl -n data-governance rollout status deploy/data-governance-interactions
```

The migrate init container lands schema head `0009_interaction_legs`. The processor
refuses to start against an unmigrated DB (exit 3) — that is the intended safety net.
`deploy/k8s/70-interactions.yaml` pins `INTERACTIONS_ALGORITHM: "sidecar"`; switching
algorithms is a *selection change plus a data reset*, never an addition —
see [`CUTOVER-two-span.md`](CUTOVER-two-span.md).

## 2 · Point the kagenti collector at DG (~1 min)

```sh
./deploy/patch-kagenti-collector.sh          # idempotent; --revert undoes it
```

Adds the `otlp/data_governance` exporter and the `transform/lineage_display`
processor to the `traces/phoenix` pipeline. If you own the kagenti-deps Helm
release, prefer the declarative form in `deploy/kagenti-collector-dg-values.yaml`
(it survives `helm upgrade`; the script needs a re-run after one).

## 3 · Adapt one app (~5 min)

Use the adapter kit in `kagenti-extensions-snp/authbridge/demos/lineage-adapter/` —
`RUNBOOK.md` is the operational recipe, `DESIGN.md` the reasoning:

```sh
./build-otel-shim.sh    # layer the propagate-only OTel shim on the app image
./attach-lineage.sh     # emit app Deployment + sidecar + parser pipeline, pipe to kubectl apply
```

Two things that matter and are easy to get wrong:

- **The caller must supply the initial `traceparent`.** Without it the sidecar
  cannot correlate outbound calls to the inbound request that caused them, and
  under concurrency everything collapses onto one inbound. The harnesses mint one
  per turn.
- **The parser chain is uniform in both directions** (`a2a`, `mcp`, `inference`).
  A direction-specific chain silently mislabels whatever it was not given — an
  MCP-entry tool with an a2a-only inbound chain records its `tools/call` as
  anonymous HTTP, which the UI then hides as infrastructure. Safe because the
  parsers are content-gated and mutually exclusive (a2a claims only `message/*`
  and `tasks/*`).

## 4 · Drive one turn (~1 min)

Drive **in-cluster** — a port-forward bypasses the sidecar and captures nothing:

```sh
SELF_ID=weather-service TARGET=weather-service.team1.svc.cluster.local:8080 \
  N=6 SETTLE=50 ./concurrency-test-interactions.sh
```

Target `6/6` clean forests, 6 distinct traces. `N=6` concurrent is the point: it is
what distinguishes real correlation from a lucky single-request path.

## 5 · What to look at (~4 min)

`http://dg.localtest.me:8080/ui/traces` → pick your trace.

- **Interaction flow** (`/flow`) — the answer. A weather turn is 19 interactions of
  which **4 are signal**: the entry call, two LLM calls, one tool call. The rest is
  MCP plumbing, hidden by default behind "N infrastructure interactions hidden —
  show".
- `?showInfra=1` reveals the plumbing; `?flat=1` shows **one row per leg**
  (request / response) instead of per interaction; `?svc=` filters the span tree.
  Every view state is in the URL by ADR-0021 — deep links work.
- **Span tree** (`/spans`) — the raw evidence. Deliberately *not* filtered: when the
  flow view says "15 hidden", this is where you confirm that is honest.

## 6 · What NOT to expect — these are correct, not bugs

- **Every trace shows "missing parent".** The entry exchange's wire parent is the
  caller's span, which DG never receives. The derivation **never guesses parents**
  (contract), so that parent stays dangling. Same reason Phoenix's *Root Spans*
  filter shows nothing — use its **Traces** tab.
- **Multi-root traces.** When the callee is also sidecarred, each of its inbound
  exchanges is its own dangling-parent root. `roots == 1` holds only for a
  single-sidecar topology — the invariant is *exactly one root is the entry
  exchange*. A weather turn is 1 root; reservation (service + tool) is 16.
- **MCP sessions are multi-root by design** — one session is several entry
  exchanges sharing the caller's traceparent.
- **No interaction for HTTPS legs.** TLS passthrough: the sidecar never sees
  plaintext. An SNI observer is a named follow-up, deliberately not built.
- **`client:(unknown)`** as the entry caller — ext_proc supplies no peer address.
  Producer-side follow-up.
- **No `http.method` on request spans**, though the contract lists it. A live wire
  observation worth confirming on the producer side.
- **A request leg with no response leg** means the call is *in flight*; duration is
  computed on read and is null until the response lands. Not missing data.
- **Two legs can share one payload row.** Payloads are content-addressed, so an
  agent relaying a body verbatim collapses both legs onto one row with one
  `content_kind`. Read kinds from the interactions API, never by counting payload
  rows.

## 7 · If you want to go deeper

| Question | Where |
|---|---|
| How does the algorithm work? | [`reading-the-sidecar-algorithm.md`](reading-the-sidecar-algorithm.md) — a 15-minute guided read |
| Why this shape, and what was traded? | `docs/adr/0028-*` (sidecar algorithm), `0025` (legs), `0026` (algorithm selection) |
| What exactly does the wire carry? | [`sidecar-wire-contract.md`](sidecar-wire-contract.md) (law) |
| Switching or resetting algorithms | [`CUTOVER-two-span.md`](CUTOVER-two-span.md) — note step 7's skipped-span assertion |
| Adapting another app | the kit's `RUNBOOK.md`; per-app evidence in its `validation/` cards |
| Does it hold under concurrency? | 10 apps × 6 concurrent turns, all 6/6 — cards in the kit's `validation/` |
