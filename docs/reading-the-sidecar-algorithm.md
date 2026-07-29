# Reading the sidecar interactions algorithm

A guided path through `INTERACTIONS_ALGORITHM=sidecar` — the derivation that turns
**AuthBridge sidecar spans** into the `entities` / `interactions` / `interaction_legs`
/ `interaction_spans` / `interaction_payloads` graph. Written to be read in about
15 minutes, in the order below.

It is one of three peer algorithms inside the P-interactions processor
(`streaming` — ADR-0007, `graph` — ADR-0026, `sidecar` — ADR-0028); exactly one
runs at a time, selected by env, sharing one cursor. See
`docs/CUTOVER-two-span.md` for switching between them.

**~720 lines across 3 files**, on upstream's legs schema (migration 0009), with
`state.py` / `procedure.py` / `graph/` untouched.

## Two spans per exchange — request + response, linked by `exchange.id`

The sidecar emits telemetry **the moment it sees each message**: one span when it
sees the **request**, a second when it sees the **response** — even for a
synchronous call that waits for its answer. (This differs from framework
instrumentation, which holds one span open and attaches both bodies at the end.)

The two spans:
- have **different span ids** (span ids are unique per span);
- carry a shared **`lineage.exchange.id`** so they're provably one round-trip —
  its value is the **request span's id**;
- split by **role** — the request span carries `input.value`; the response span
  carries `output.value` plus the outcome.

**Why this shape.** Emit-on-sight buys immediacy (the request is visible at call
time, not only when the response returns), crash-safety (the request is recorded
even if no response comes), and a simpler producer (no open span to hold, no
request body to buffer). The cost: DG pairs two spans — a bounded, id-based
pairing, chosen deliberately. It also diverges from framework's one-span shape, so
the two sources re-converge only at the **interaction** level, not the span level.

The wire is fixed by `docs/sidecar-wire-contract.md` (v1.1) — that document is law.

### How the interaction is keyed, and where each span lands

`interaction_id = uuid5(NS, "{trace_id}/{exchange_id}")` — both spans map to the
SAME interaction. Because the exchange id *is* the request span id, this formula
coincides with the id the other algorithms mint, so a cutover re-derive lands on
the same rows (verified live: interaction ids are set-identical across a
truncate-and-rebuild).

The identity/leg split is ADR-0025's, and it fits this producer exactly:

- **`interactions`** — one row: identity only (caller, callee, parent, summary).
- **`interaction_legs`** — one row per temporal half, PK `(interaction_id,
  leg_type)`. The **request** leg carries the request payload and `started_at`;
  the **response** leg carries the response payload, `ended_at` and the outcome.

A request leg with **no response leg yet** is the honest representation of a call
**in flight** — duration is computed on read and is null until the response lands.
The pre-legs single-row shape could not express that at all.

Whichever span arrives first creates what it can; the other fills its half. Still
deterministic, idempotent, order-independent.

## Read in this order (≈15 min)

1. **`data_governance/sidecar_facts.py`** *(the vocabulary — start here).* The
   shared leaf module naming every wire fact and content kind. It is deliberately
   dependency-free so both the derivation and the classification projection can
   import it without a layering cycle.

2. **`processors/interactions/sidecar.py` → `classify()`** *(the seam).* One
   request span → its `Kinds` (protocol, mcp method, request/response content
   kind). **All meaning lives here.** The producer emits facts; every judgment
   about what those facts *mean* — including which exchanges are infrastructure
   (`mcp_lifecycle_*` / `tool_discovery_*`) — is made in this function and its
   table. When the vocabulary changes, you change this and nothing else.

3. **`sidecar.py` → `plan_trace()`** *(the whole derivation, pure).* Takes every
   span of one trace and returns a `_Plan`: entities, rows, legs, payloads,
   ownership. Note `_nearest_anchor` / `_reaches` — the parenting rule, which never
   *guesses* a parent (the contract forbids it): a dangling wire parent stays
   dangling, which is why a trace observed by two sidecars is legitimately
   multi-root.

4. **`sidecar.py` → `_legs_of()` and `_write()`** *(the write boundary).* Where one
   internal row becomes 1 identity row + 2 leg rows. This is the deliberate
   divergence from upstream's shared `state.flush`: our reconcile can legitimately
   **shrink** (an inbound entry is a real interaction until its outbound ancestor
   arrives, at which point it must be deleted and folded in as the callee echo),
   and `flush`'s anchors are emit-once. ADR-0028 records the trade.

5. **`sidecar_driver.py` → `process_span()` / `drain()`** — the cursor pattern:
   each arriving span triggers a **full re-derive of its trace**. That is what
   makes arrival order irrelevant; re-processing is an idempotent upsert.

6. **`tests/processors/interactions/test_sidecar_write_integration.py`** — the
   fastest way to see expected output, including the **shrinkage** case that pins
   the anchor-demotion semantics above.

## The mental model (all of it)

- One interaction per **outbound hop** + per **entry (root) inbound hop**.
- **Non-root inbound spans are folded in as the callee echo** of a parent outbound
  hop already counted. *That fold is the entire dedup.*
- Endpoint **kinds come from the wire facts** — no inference, no guessing parents.
- Each span triggers a re-derive of its trace; deterministic ids make that an
  idempotent upsert — no waiting for the trace, no retraction.
- **Payloads** are content-addressed from `input.value` / `output.value`. Note the
  consequence: when an agent relays a body verbatim, two legs share **one** payload
  row carrying **one** `content_kind` — so payload kinds are not a sound way to
  count legs. Read kinds from the interactions API instead.

## Files

| Path | What |
|---|---|
| `data_governance/sidecar_facts.py` | the shared wire vocabulary (dependency-free leaf) |
| `data_governance/processors/interactions/sidecar.py` | `classify` (seam) + `plan_trace` (pure derivation) + `_write` |
| `data_governance/processors/interactions/sidecar_driver.py` | `process_span` / `drain` — cursor + re-derive |
| `data_governance/retrieval/interactions.py` | read-time kinds (guarded: non-sidecar anchors → `null`) |
| `tests/processors/interactions/test_sidecar_derive.py` | pure `classify` / planning tests (no DB) |
| `tests/processors/interactions/test_sidecar_write_integration.py` | end-to-end, order-independence, shrinkage vs a real Postgres |
| `tests/processors/interactions/test_sidecar_otlp_replay.py` | wire replay on the legs schema |
| `tests/processors/interactions/sidecar_golden.py` | the golden trace fixture |

## Run it

```bash
# tests (integration needs a container runtime; this override is for a podman Mac)
DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')" \
  .venv/bin/python -m pytest tests/processors/interactions/ -q
```

Then eyeball a known trace in the UI — `/ui/traces/{id}/flow` (add `?showInfra=1`
to reveal the hidden infrastructure exchanges, `?flat=1` to see one row per leg).

## Deferred (known, on purpose)

- **`client:(unknown)`** — the entry caller has no identity because ext_proc
  supplies no peer address. A producer-side follow-up;
  `caller_inference.py` does not solve it (its attribute ladder reads names this
  wire contract does not emit).
- **Host ↔ self-identity reconciliation** — a callee seen only by host vs by its
  own self-id remains two entities.
- **Tool-echo identity** is proven offline only; no live trace currently produces
  a NULL-`leg_type` echo connector.
