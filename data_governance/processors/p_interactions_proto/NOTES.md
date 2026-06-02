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

## Round 3 verdicts (2026-05-28)

The round-2 module (a single 3-pass batch extractor) was rewritten to
match the design captured in CONTEXT.md and ADRs 0007–0010. The rewrite
splits the logic across three modules:

- `caller_inference.py` — entity-kind ladder (per ADR-0009).
- `anchor_rules.py` — five structural anchor rules (per ADR-0009).
- `procedure.py` — per-span 9-step procedure (per Q14 / ADR-0007),
  driven by an in-memory `Processor` that consumes spans in seq order.
- `cli.py` — Q21 schema in `proto_*` tables (with
  `parent_interaction_id`, `interaction_span_role`, `entity_spans`,
  `processor_state`).

The `extract()` entry point still takes a list of spans (batch shape) so
the existing CLI continues to work; the per-span procedure inside is the
shape that will run inside one transaction per span when the streaming
driver lands.

### Numbers on trace `05c6095d1f863dcb3b209ef4761829e1` (498 spans)

|                     | round 2 | round 3 (in-order) | round 3 (--scramble) |
|---------------------|---------|--------------------|----------------------|
| entities            | 18      | 20                 | 21                   |
| interactions        | 36      | 51                 | 63                   |
| top-level           | n/a     | 3                  | several              |
| unique payloads     | 32      | 36                 | 36                   |

Entity inflation vs. round-2 (20 vs. 18) comes from ADR-0010: the
in-process tool entity *and* the deployed-tool entity are both kept now
(`book_flight` appears as both `tool:agent:(...,booking-agent):book_flight`
and `tool:(dl-demo,book_flight)`). The corresponding interaction count
roughly doubles for MCP-heavy tool calls, which is the documented
consequence of ADR-0010.

### Reversed: round-2 Problem 1 (subtree suppression of in-process tools)

Per ADR-0010 the round-2 dedup is gone. The openinference-tool and
cross-service rules both fire independently; the interaction tree
(ADR-0008) connects them. `book_flight`, `book_hotel`,
`send_notification` each produce both an `agent → tool` (in-process)
interaction and a `tool → tool` (cross-service) interaction.

### Still deferred: round-2 Problem 2 (`service:travel-advisor` collision)

Three spurious `service:travel-advisor` / `service:research-agent` /
`service:booking-agent` entities still appear, plus a corresponding
`service:ete-litellm.ai-models...` for the LLM. They come from
demo-client's CLIENT POSTs whose `http.url` host (`travel-advisor`)
doesn't match the canonical service name (`dl-demo-travel-advisor`).
Round 2 deferred this to v2 (requires a real entity inventory or k8s
service catalogue); round 3 inherits the wart. The retraction
heuristic in `procedure._retract_misfired_external_http` only catches
the case where the host string and a canonical match exactly, which
they don't here because of the `dl-demo-` prefix.

### Verified expectations from the handoff

- `demo-client` is correctly identified as a `client` entity, not an
  `agent`, via the "service emits only CLIENT/INTERNAL spans"
  heuristic.
- Span `c48ed5c0cf192c40` (get_weather) is the `anchor` of its
  openinference-tool interaction and `info` on the enclosing
  cross-service `demo-client → dl-demo-travel-advisor` interaction.
- `demo_turn_1` and `demo_turn_2` are `connector` rows on the
  cross-service `demo-client → dl-demo-travel-advisor` interaction
  (caller-side connector chain attached in `result()`).
- The trace root `demo_two_turn_conversation` is **not** attached to
  any interaction — per ADR-0008. The walker stops one ancestor
  short of the real root.
- All seven standalone tools are present as in-process `tool`
  entities (and three as deployed `tool` entities).
- All `delegate_to_*` agent-consultation spans are excluded from the
  entity graph (the anchor rule filters them by
  `kagenti.call.kind`).

### Speculative-firing policy (deferred decisions)

The handoff's "external-HTTP misfire under streaming is OK" policy
(LE2) is implemented as a **deferred-spans queue**: SERVER spans whose
`parent_id` is set but parent isn't yet in the in-memory state are
deferred rather than eagerly fired as orphan-server. The original
implementation fired orphan-server speculatively for every SERVER
seen ahead of its parent in seq order and produced 200+ provisional
`(unknown client)` entities. The new policy:

- Fire orphan-server only on `parent_id IS NULL` (real root SERVER) or
  on batch-end with `parent_id_known_unresolvable=True`.
- When the parent eventually arrives, re-fire `cross-service` on the
  child via `_reprocess_resolved_deferred`.
- This deviates slightly from a pure streaming model: the streaming
  driver still wants speculative firing + late-parent demotion, but
  with bounded time-to-defer (a heartbeat) so that genuine orphans
  don't sit in the queue forever. Pencil this in for the streaming
  driver's plumbing decisions.

### Late-parent simulation (`--scramble`)

`cli.py --scramble` reverses span-arrival order so every child arrives
before its parent — a torture test for late-parent re-evaluation.
Running it on the demo trace produces 63 interactions vs. 51 in seq
order. The extra 12 interactions are interactions whose caller
identity got demoted from a provisional client to the real
agent/client at late-parent time, but where the original
caller-entity row was left in `entities` and a new interaction was
sometimes created instead of mutating the existing one. **Round-3
known limitation:** the swap-and-orphan path mutates `caller_entity_id`
correctly but does not always converge to the same final shape as
in-order processing. Acceptable for the prototype; the streaming
driver will need a stricter idempotence story (cursor-driven
re-processing + interaction `seq` watermark per ADR-0007).

### Schema differences from production-spec (Q21)

The prototype mirrors the Q21 schema as `proto_*` tables but with two
deviations to keep alembic out of the loop:

- ENUMs (`entity_kind`, `entity_span_role`,
  `interaction_span_role`) are stored as `text` in the prototype.
- `proto_interactions` has an `anchor_rule` debug column not in the
  production schema, plus a prototype-only `trace_id` column on
  `proto_entities` to make scratch-table cleanup easy.

### Module shape lessons

- The five-rule, kind-agnostic split (ADR-0009) made the rewrite
  straightforward to test mentally. No anchor rule inspects entity
  identity attributes; no caller-inference rule inspects span
  structural relationships.
- The interaction tree (ADR-0008) computed from the primary anchor's
  parent chain is one short function and produces correct nesting
  on the demo trace.
- The "innermost-territory" attachment rule needed two clarifying
  patches mid-rewrite: (a) walking up the *primary anchor's parent
  chain only* (not all anchors of a multi-anchor decision), and (b)
  stopping the walk at the parent of the trace root (the root itself
  is excluded per ADR-0008).
