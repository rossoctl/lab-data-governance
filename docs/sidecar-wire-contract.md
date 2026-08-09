# Sidecar wire contract — two-span lineage (v1.5.1)

The single source of truth for what the AuthBridge lineage plugin emits and what the
P-interactions `sidecar` algorithm (ADR-0029) consumes. Fixes the attribute names that were left
"pending confirmation". Producer: `kagenti-extensions-snp/authbridge/authlib/plugins/lineage/`.
Consumer: `data_governance/processors/interactions/sidecar.py` (vocabulary:
`data_governance/sidecar_facts.py`).

Principles (agreed 2026-07-21):
- **Facts, not meaning.** The sidecar emits what it observed on the wire plus parsed protocol facts.
  All vocabulary (hop kinds, entity kinds, caller/callee) lives in the consumer's `classify()`.
- **Emit on sight.** Two spans per exchange, each emitted as soon as its half is seen. No open span
  held across the wait, no request body buffered for the exchange's lifetime.
- **One channel, never traceparent (v1.5).** The sidecar parent chain lives entirely in the
  `dg-parent` tracestate member: every lineage element — inbound and outbound alike — reads its
  parent from the stamp (else the wire parent) and re-stamps the member with its own request
  span id. The forwarded traceparent is never modified; the v1.4 outbound splice is removed.
  Rationale: the sidecar's spans and an app's own spans land in different backends, so
  cross-pointing ids always dangles somewhere — the stamp keeps the sidecar chain
  self-consistent in this store while an app that emits its own spans keeps an intact
  traceparent chain toward its own backend. **All attribution fallbacks remain removed**
  (`agentCurrentInbound`, inbound seed-inject, and as of v1.3 the trace-keyed inbound map): an
  element is attributed by the stamp, else the wire parent — nothing else.
- **No mechanism may guess.** A mechanism whose correctness depends on a precondition it cannot
  verify at runtime does not belong in the producer. When attribution is unknown the sidecar says
  so (the wire parent, `lineage.parent.source=wire`) and the edge is visibly absent. A missing edge
  is recoverable downstream; a confidently wrong one is not, because it is indistinguishable from a
  true one.
- **Parsers reduce payloads.** `input.value`/`output.value` are semantic content produced by the
  a2a/mcp/inference parser plugins, not raw bytes. Two documented heuristics live in this
  reduction (payload enrichment only — interactions are never affected): the a2a parser falls
  back to the *status message* text when a result carries no artifact, and the lineage plugin
  suppresses A2A protocol events from `output.value` by matching event-kind substrings
  (`status`, `artifact-update`, `working`, `canceled`). Both can mislabel unusual payloads;
  since payload absence is contract-legal, the failure mode is a missing or imprecise
  `output.value`, never a wrong interaction.
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
therefore means one of exactly two things: the sidecar itself died mid-exchange, or the plugin
recovered a panic while emitting the response span (WARN logged) — rendered as in-flight, never
a wrong pairing. A response span whose `lineage.outcome` is somehow absent derives with
`error=NULL` (honest unknown), never `false`.

**Scope limit on `denied`:** the lineage plugin runs after the gate plugins, and the pipeline
short-circuits on a request-phase denial — an exchange a gate rejects **before** the request span
was recorded emits **no spans at all** and is invisible to lineage. `denied` therefore appears
only for denials after the request span exists (response-phase denials, or gates ordered after
lineage). Emitting spans for gate-denied traffic (lineage ahead of the gates) is a named
follow-up, not current behavior.

## Identifiers and parenting

- **`lineage.exchange.id` = the request span's span_id**, echoed on both spans. No new identifier
  is minted; the response span simply names its request twin.
- **The tracestate stamp (v1.2, both directions since v1.5).** Each lineage element re-stamps
  one W3C `tracestate` member on the request it forwards: `dg-parent=<its own request span_id>`.
  Inbound stamps toward its own app — the app's propagate-only shim carries tracestate through
  its per-request causal chain (contextvars), so the member surfaces on exactly the outbound
  calls that inbound caused. Outbound re-stamps toward the peer, whose inbound sidecar reads it
  as its parent. The intra-pod leg is the one that stays unambiguous under CONCURRENT
  same-trace inbound exchanges to one pod — the trace-keyed map holds one entry per trace and
  collapses there (proven live 2026-07-30: 6 concurrent same-trace turns through a mid-chain
  agent paired 1/6 by map, 6/6 by stamp; cross-trace concurrency was and stays 6/6). Foreign
  tracestate members are preserved; the stamp requires a valid wire traceparent (without one
  the shim roots a fresh trace and drops tracestate anyway). The key names the consuming
  data-governance system (W3C convention: the key identifies the entry's owner) and is
  deliberately platform-neutral; it was `kglin` until 2026-08-04 — the name never lands in
  stored data, so the rename is wire-only.
- Request span parent: the tracestate stamp (`dg-parent` — the previous lineage element: the
  caller sidecar's outbound for an inbound, this pod's inbound for an outbound), else the wire
  parent. Same precedence in both directions; there is no third option. Malformed stamps fall
  through to the wire parent silently.
- **Forwarded traceparent is NEVER rewritten (v1.5).** The only header the sidecar mutates is
  the `dg-parent` tracestate member. History: v1.2–v1.4 rewrote the outbound traceparent to name
  the request span (the splice); the rewrite was inert in the deployed envoy-sidecar
  (ext_proc) mode until v1.4 made the header diff live on all four handler paths (2026-08-03 —
  the mechanical cause of the phantom-rooted per-pod trees stored before that date). v1.5
  moves the cross-pod link to the stamp: a callee sidecar's inbound request span is parented
  on the caller sidecar's outbound request span via `dg-parent`, so the exchange-merge (tool-echo
  identity) still fires across pods, and traceparent is left to whatever chain the app itself
  maintains. Traces still enter with ONE dangling parent at the trace edge (the un-sidecared
  driver/UI), by design.
- **The trace-keyed map is gone (v1.3).** It answered from "the last inbound seen for this trace",
  which is correct only while exactly one inbound of that trace is in flight — a precondition it
  never checked and could not verify. Under same-trace concurrency it produced a real, exported,
  walkable parent that was simply untrue, and no signal that it had done so. Measured on the live
  fleet before removal: **zero** spans were ever attributed via the map (`parent.source` census
  2026-07-31: `tracestate` 138 outbound, `wire` 35 inbound, `map` 0), because every shimmed app
  couriers the stamp.
- **Consequence for un-stamped traffic** (an app with no propagate-only shim, one that strips
  `tracestate`, or a caller with no sidecar): the element falls to the wire parent and records
  `lineage.parent.source=wire`, whose span id is typically a span this pipeline never exported.
  The exchange still derives into a complete, first-class interaction — it simply has no parent
  anchor, so it renders as a trace entry rather than a child. The trace fragments at that pod,
  visibly, instead of being welded with a guess.

## Attributes

Resource (unchanged): `service.name=authbridge`, `authbridge.component=lineage-telemetry`.

| key | on | example | notes |
|---|---|---|---|
| `lineage.exchange.id` | both | `00f067aa0ba902b7` | = request span_id, hex |
| `lineage.role` | both | `request` \| `response` | which half this span is |
| `lineage.direction` | both | `inbound` \| `outbound` | |
| `lineage.self.id` | both | `weather-service` | from `self_id` / `self_id_file` |
| `lineage.peer.addr` | *(removed in v1.4)* | `10.244.2.5:47312` | REMOVED from the producer 2026-08-03. It was inbound-only and never produced in the deployed envoy-sidecar (ext_proc) mode, where the remote address is unavailable to the plugin — so it served nothing live and was dropped rather than kept as proxy-mode-only surface. Anonymous inbound callers derive as `client:(unknown)`, as before. Spans stored before v1.4 from proxy-mode sidecars may carry it; the consumer must tolerate but derives nothing from it. Reintroduction (with an ext_proc source for the address) is a possible follow-up |
| `lineage.peer.host` | both | `weather-tool-mcp.team1.svc:8000` | Host/authority header when present |
| `lineage.protocol` | both | `a2a` \| `mcp` \| `inference` \| `http` | which parser matched; `http` = none |
| `lineage.parent.source` | request | `tracestate` \| `wire` | v1.3: which mechanism chose the request span's parent — the tracestate stamp (exact) or the wire traceparent. v1.5: both directions are stamp-first, so inbound spans carry `tracestate` too (any inbound whose caller has a sidecar); spans stored before v1.5 have inbound always `wire`. `map` was a legal value in v1.2 only; stored spans predating v1.3 may still carry it. A fact for auditing attribution; the consumer derives nothing from it |
| `http.method` | request | `POST` | standard OTel key, emitted when the listener supplies the method. As of the 2026-08-02 upstream merge all three listeners do (reverse/forward proxy from `r.Method`, ext_proc from `:method`); spans stored before that merge lack it |
| `url.path` | request | `/mcp` | standard OTel key |
| `url.scheme` | request | `http` | standard OTel key; added v1.5.1 (2026-08-09) so a consumer can compose a full destination URL (`scheme://peer.host + url.path`). From the listener's observed scheme (ext_proc `:scheme` pseudo-header / `r.URL.Scheme` in the proxies); emitted only when non-empty. Spans stored before v1.5.1 lack it — the consumer treats it as optional and composes no URL without it (no guessing) |
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
