# Sidecar wire contract — two-span lineage (v1, for review)

The single source of truth for what the AuthBridge lineage plugin emits and what the
P-interactions `sidecar` algorithm (ADR-0028) consumes. Fixes the attribute names that were left
"pending confirmation". Producer: `kagenti-extensions-snp/authbridge/authlib/plugins/lineage/`.
Consumer: `data_governance/processors/interactions/sidecar.py` (vocabulary:
`data_governance/sidecar_facts.py`).

Principles (agreed 2026-07-21):
- **Facts, not meaning.** The sidecar emits what it observed on the wire plus parsed protocol facts.
  All vocabulary (hop kinds, entity kinds, caller/callee) lives in the consumer's `classify()`.
- **Emit on sight.** Two spans per exchange, each emitted as soon as its half is seen. No open span
  held across the wait, no request body buffered for the exchange's lifetime.
- **The splice stays.** One deterministic header rewrite on outbound plus the trace-keyed inbound
  map — the standard mesh behavior that keeps the exported parent chain walkable. All fallbacks
  (`agentCurrentInbound`, inbound seed-inject) are removed.
- **Parsers reduce payloads.** `input.value`/`output.value` are semantic content produced by the
  a2a/mcp/inference parser plugins, not raw bytes.
- **Interactions are independent of payloads.** Every exchange the sidecar saw becomes a full
  interaction — kind, endpoints, timing, status — whether or not a body could be read (unparsed
  protocol, streamed response, `capture_io` off, or encrypted content). Payload columns are
  enrichment; their absence is never a reason to drop, downgrade, or hide a hop. (TLS-passthrough
  connections are a *capture* gap, not a derivation rule: they bypass the HTTP pipeline entirely
  and produce no exchange today; making them seen is a named follow-up — once seen, same rule.)

## Span model

One HTTP exchange through the sidecar → two OTLP spans:

| | request span | response span |
|---|---|---|
| emitted | when the request (headers+body) has been seen and forwarded | when the response has been fully relayed (or stream ends / errors) |
| SpanKind | SERVER (inbound) / CLIENT (outbound) | same as its request span |
| parent | see "parenting" below | its request span |
| carries | caller-side facts + `input.value` | status/error facts + `output.value` |

Exchange duration = response.end − request.start (computed downstream). The response span is
emitted at stream end **even when no response was produced** (client disconnect, upstream reset,
plugin denial) — it then carries `lineage.outcome` (`ok` | `denied` | `error` | `abandoned`) and
whatever status exists, so the row completes as failed instead of dangling. A lone request span
therefore means exactly one thing: the sidecar itself died mid-exchange — rendered as in-flight,
never a wrong pairing.

## Identifiers and parenting

- **`lineage.exchange.id` = the request span's span_id**, echoed on both spans. No new identifier
  is minted; the response span simply names its request twin.
- Request span parent: inbound → the wire traceparent's parent; outbound → this pod's inbound
  request span for the same trace_id (the trace-keyed map), else the wire parent.
- Forwarded traceparent (outbound only) is rewritten to name the request span as parent — the
  splice. Inbound requests are forwarded with headers untouched.
- The map keeps entries 5 minutes past exchange finish (SSE-drop tolerance; documented, not tuned).

## Attributes

Resource (unchanged): `service.name=authbridge`, `authbridge.component=lineage-telemetry`.

| key | on | example | notes |
|---|---|---|---|
| `lineage.exchange.id` | both | `00f067aa0ba902b7` | = request span_id, hex |
| `lineage.role` | both | `request` \| `response` | which half this span is |
| `lineage.direction` | both | `inbound` \| `outbound` | |
| `lineage.self.id` | both | `weather-service` | from `self_id` / `self_id_file` |
| `lineage.peer.addr` | both spans, **inbound only** | `10.244.2.5:47312` | the direct TCP caller's address. Not emitted on outbound — there the proxy only observes the app's own socket (and nothing at all under ext_proc), which would mislabel the fact; outbound callee identity comes from `peer.host` |
| `lineage.peer.host` | both | `weather-tool-mcp.team1.svc:8000` | Host/authority header when present |
| `lineage.protocol` | both | `a2a` \| `mcp` \| `inference` \| `http` | which parser matched; `http` = none |
| `http.method` | request | `POST` | standard OTel key |
| `url.path` | request | `/mcp` | standard OTel key |
| `a2a.method`, `a2a.session_id` | request (a2a) | `message/send` | parsed facts |
| `mcp.method`, `mcp.tool` | request (mcp) | `tools/call`, `get_weather` | tool name only for `tools/call` |
| `inference.model` | request (inference) | `qwen2.5:7b` | from parsed request body |
| `input.value` | request, `capture_io` | `{"city":"Tokyo"}` | semantic reduction by parsers |
| `output.value` | response, `capture_io` | `{...}` | absent when unparsed/streamed — accepted |
| `http.status_code` | response | `200` | standard OTel key; absent if none was produced |
| `lineage.outcome` | response | `ok` \| `denied` \| `error` \| `abandoned` | how the exchange ended, as the proxy saw it |
| `lineage.denied_by` | response (denials) | `jwt-validation` | plugin that denied, if any |
| `lineage.principal.sub`, `lineage.principal.client` | request, inbound, when JWT validated | `alice` | raw identity facts |

Span names: request = `{self.id} {protocol} {op}` where op = mcp.tool / a2a.method /
inference.model / url.path; response = same + ` response`.

## Removed vs. today's emitter (migration map)

| today | replacement |
|---|---|
| `lineage.hop.kind`, `trust.hop_kind` | consumer `classify()`: (direction, protocol[, mcp.method]) → kind table |
| `lineage.source.id`/`target.id`, `trust.source_id`/`target_id` | `self.id` + `peer.*` + `direction`; caller/callee computed downstream |
| `enduser.id`, `trust.principal_id` | `lineage.principal.*` |
| `source=sidecar` | resource `service.name=authbridge` already says it |
| `openinference.span.kind` | not emitted; if the Phoenix pipeline needs it, a collector transform adds it (meaning belongs in infra) — verify collector filter before rollout |
| anonymous-inbound "emit but omit hop.kind" suppression | gone; every inbound emitted uniformly; consumer folds anonymous callers |
| `is_principal` config reclassification | consumer-side config if ever needed |

## Consumer commitments (the sidecar interactions algorithm)

- Interaction id = `uuid5(NS_INTERACTION, f"{trace_id}/{exchange.id}")`. Request half fills
  caller/callee/request_payload_hash/started_at; response half fills response_payload_hash/
  ended_at/error. Whichever arrives first creates the row (idempotent upsert; in-flight visible).
- Anchors: role=request AND (direction=outbound, OR direction=inbound with no stored ancestor —
  entry detection must tolerate a *dangling* wire parent, since driver/UI root spans are never
  exported; "parent_id IS NULL" alone is insufficient).
- Response spans are never anchors; they attach by exchange.id.
- Kinds and entity identity come from the facts table only; `classify()` must never require
  `input.value`/`output.value` to exist — bodyless exchanges produce complete, first-class
  interaction rows with NULL payload hashes, and the UI renders them like any other row.
- `content_kind` vocabulary stays ADR-0014-compatible (P-classification consumes
  `interaction_payloads` as a stream).
- Drain loop = shared `data_governance/processors/_driver.py` StreamSpec.

## Plugin config after rewrite

Kept: `otel_endpoint`, `capture_io`, `bypass_paths`, `bypass_hosts`, `self_id`, `self_id_file`.
Removed: `is_principal`; `emit_body_hash` (drop unless a consumer is found).

## Open items

1. Verify the kagenti collector's phoenix/lineage filters against the new attribute set (does
   Phoenix filter on `openinference.span.kind`? If yes, add the transform).
2. Naming taste: `lineage.self.id`/`lineage.peer.*`/`lineage.principal.*` — flag objections now;
   renames are cheap while the seam is open, expensive after.
3. Response-span name suffix (` response`) — cosmetic, Phoenix legibility only.
