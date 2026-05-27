# P-interactions prototype — findings

Trace used: `05c6095d1f863dcb3b209ef4761829e1` (498 spans, 7 services, demo
travel/research/booking agents + 3 MCP tools + LLM).

The prototype identified **18 entities** and **36 interactions** with the
algorithm in [extractor.py](extractor.py). What looked clean on paper
surfaced four real problems against actual data — exactly what a prototype
is for.

## Problem 1 — duplicate counting of cross-service tool calls

The booking-agent calls `book_flight` (a local OpenInference TOOL span on
the agent's process) **and** emits a CLIENT POST → SERVER `POST /mcp` to
the deployed `dl-demo-book_flight` service. The prototype creates **two**
interactions:

```
ok   dl-demo-booking-agent → book_flight (tool)         <- local TOOL anchor
?    dl-demo-booking-agent → dl-demo-book_flight (POST /mcp)  <- SERVER anchor
```

These are evidence of the **same logical call**. The algorithm's two
anchor passes (cross-service SERVER, then local TOOL) overlap when the MCP
"local tool" is in fact a remote tool service.

### Resolution candidates to grill next

- **Order anchors and dedupe by descendant containment.** If a SERVER
  anchor's evidence subtree contains a TOOL-kind INTERNAL span at the
  caller, suppress the TOOL-anchor interaction — the SERVER-anchor
  interaction already covers it. The TOOL span becomes evidence on the
  cross-service interaction.
- **Add a `local` flag to the tool entity and only emit local-tool
  interactions when the call is genuinely in-process** (no descendant
  CLIENT span that crosses to a SERVER on a different service).

## Problem 2 — `external_service: travel-advisor` (entity-identity collision)

`demo-client`'s CLIENT POST to the travel-advisor agent has
`http.url = http://travel-advisor` (k8s Service name), which resolves to a
different string than the registered `service.name = dl-demo-travel-advisor`.
The prototype's "host in known_service_names" check failed and produced a
spurious `external_service: travel-advisor` entity.

Then *another* CLIENT POST in the same trace (the agent-to-agent one) that
**did** have a SERVER child correctly produced the
`travel-advisor → research-agent` interaction. So the bug is purely in the
external-service detection's hostname canonicalization.

### Resolution candidates

- **Drop CLIENT-with-SERVER-child from external-service consideration**
  before host extraction. (Already done — but the failing case has no
  SERVER child because demo-client's CLIENT POST is the *parent* of the
  agent's SERVER, so the SERVER *does* exist as a child. Need to re-check
  the implementation.)
- **Resolve hostnames against a known set of k8s Service names**, not just
  span-emitting `service.name` values. Requires entity inventory the
  Caller inference rule could consult — flagged as a v2 add.

## Problem 3 — 4 LLM calls dropped ("no external_service callee")

Four `generation` spans on research-agent and booking-agent had no CLIENT
POST descendant carrying `http.url`. Likely those LLM calls used a
different transport (in-process litellm, or the http.url was on a different
attribute key like `url.full`). Worth investigating per-instrumentation
quirks before the algorithm assumes "every LLM `generation` has a CLIENT
POST descendant with http.url".

### Resolution candidates

- **Fall back to `llm.invocation_parameters.api_base` or `gen_ai.system`**
  when http.url is absent.
- **Allow LLM interactions with a synthetic "external_service: (LLM)"**
  callee when no host can be determined — better than dropping them
  silently.

## Problem 4 — `?` status on every cross-service interaction

The status column shows `?` (unset) on every cross-service hop. That's
because the boundary SERVER spans carry `error IS NULL` (OTLP `Status.UNSET`
— the agents' instrumentation doesn't set Status). The error column is
fine on the OpenInference INTERNAL spans (`error IS FALSE`), which is why
LLM and local-tool interactions show `ok`.

### Resolution candidate

- **Aggregate error from descendants.** An interaction's `error` should be
  `TRUE` if *any* of its evidence spans has `error IS TRUE`, and `FALSE`
  only when at least one evidence span is `FALSE` and none are `TRUE`. Pure
  UNSET stays NULL.

## What the prototype confirmed (worth keeping)

- **Entity-kind heuristic ladder works** for agent / tool / external_client
  on real OpenInference-instrumented services. Detection notes are
  human-readable.
- **OpenInference `generation` spans are the right LLM anchor.** They carry
  `llm.input_messages.*` and `llm.output_messages.*`, which canonicalize
  cleanly into chat-prompt and chat-completion payload rows.
- **Payload dedup works.** 36 interactions produced 32 unique payload rows
  (5 chat prompts, 5 completions, 11 tool args, 11 tool results) — content
  hashing already collapses duplicate prompts.
- **Anchor-on-outermost-SERVER correctly excludes intra-service framework
  plumbing.** Without this rule the trace would have produced ~250 SERVER
  interactions (a2a / FastAPI inner servers); with it, 16.
- **`interactions.id = uuid` + linking-table evidence** keeps the
  interactions table source-agnostic. The OTEL anchor is recoverable from
  `proto_interaction_spans` when needed; nothing about
  `proto_interactions` references span columns.

## Next round of grilling, suggested order

1. Problem 1 (dedup local-tool / cross-service overlap) — load-bearing for
   correctness of the entity graph.
2. Problem 4 (error aggregation rule) — small, easy, clearly correct.
3. Problem 2 (external-service hostname canonicalization) — depends on
   what entity inventory we ever build.
4. Problem 3 (LLM transport-detection) — instrumentation-specific tail.

## Round 2 verdicts (2026-05-26)

After the second round of trace inspection and user feedback, the
extractor was patched and rerun. Outcomes:

### Problem 1 — RESOLVED

A TOOL anchor whose subtree contains a SERVER on a different service is now
suppressed; the cross-service SERVER anchor wins. `book_flight` /
`book_hotel` no longer produce duplicate local-tool interactions.

### Problem 2 — DEFERRED to v2

`external_service: travel-advisor` still appears alongside
`dl-demo-travel-advisor`. The fix needs a real entity inventory (k8s
Service inventory or a registered-agent registry) the Caller inference
rule can consult — not something the prototype should build.

### Problem 3 — RESOLVED (anchor moved)

The LLM anchor was relocated from the CLIENT POST descendant to the
OpenInference LLM-kind span itself. The model is always recoverable from
that span, so no LLM call is dropped. Host falls back to descendant
http.url and ultimately to `(unknown)`.

### Problem 4 — RESOLVED

`_aggregate_error` bubbles `error=true` up from any descendant; the cross-
service ERR rows propagate correctly. Pure-UNSET subtrees still surface as
`?`, which is the right outcome for MCP tool services that don't emit
OpenInference INTERNAL spans.

## New design refinements from round 2

### `kagenti.call.kind = agent_consultation`

OpenInference TOOL spans carrying `kagenti.call.kind == agent_consultation`
are agent-to-agent **delegation primitives** (`delegate_to_research_agent`
etc.), not standalone tools. They are filtered out of the entity graph
and instead attached as evidence on the corresponding cross-service
`agent → agent` interaction. The TOOL span's payload (`input.value` /
`output.value`) becomes the interaction's request/response payload.

The walk that finds the delegation TOOL span ancestor stops at the first
service-boundary crossing — otherwise an outer agent's delegation can be
falsely attributed to a deeper interaction (e.g. travel-advisor's
`delegate_to_booking_agent` would have been pinned onto every
`booking-agent → book_flight` MCP call).

### `llm` entity kind added

A 6th entity kind. Identity is `(host, model)`. The litellm provider
prefix on model names (`openai/<model>`) is stripped — provider routing
is plumbing, not identity. When the same model is observed with both a
known host and an `(unknown)` host, the unknown-host entity is collapsed
into the known one (instrumentation gap, not a different endpoint).

### Real-tool filter

OpenInference TOOL spans become `tool` entities **only** when
`kagenti.call.kind != agent_consultation`. On the demo trace this leaves
exactly the 7 standalone tools the user identified (`search_destinations`,
`get_weather`, `get_flights`, `get_hotels`, `book_flight`, `book_hotel`,
`send_notification`).
