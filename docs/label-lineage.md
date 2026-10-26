# Label lineage

A classifier finds **units** in payloads (an SSN, a name, a booking reference). Label lineage says,
for every payload of a trace, which earlier units are still present in it, and gives the risk engine
those labels as if the classifier had found them there. It is a second derivation beside data
lineage (ADR-0028, untouched): the same legs, the same inbound routing
(`traversal.inbound_sets`), the same classifier output.

## Terms

- **unit** — one classifier finding as an origin of labels: where it was found (trace, leg,
  payload hash), its type, tags, level, and its **value**.
- **value** — what the finding denotes, read from the projected text. For a numeric type the
  extent is the digit run the finding sits in (the classifier marks `123` of `123-45-6789`; the
  value is the whole number), bounded by `MAX_DIGITS`; for a text type it is the finding itself.
  Same-type findings whose values lie inside one another in one payload are one unit.
- **occurrence** — a unit present at one payload: the span it occupies and the run of the value
  it holds. At its origin a unit is an occurrence too.
- **edge** — an occurrence at a carrier (the from leg) found again at an output (the to leg).
  The origin rides on every edge, so a reader never walks a path.
- **finder** — `LABEL_MATCHER`; `exact` (the one registered, the default) finds the held run
  byte for byte, bounded by a non-alphanumeric character on both sides, at every place it occurs;
  a number must be the whole number there (`123-45-6789` is not found inside `7788-123-45-6789`).
  No case fold, no separator reading, no digit comparison. Identifier and content are the same
  thing under exact match.

## The fold at one leg

Legs are walked in wire order, `(occurred_at, seq)`: `seq` is not causal on the lab traces (the
egress request can carry a lower seq than the store read that fed it). At each payload-bearing leg:

1. **inputs** — the leg's inbound legs (requests reach the callee, responses the caller; agents
   keep every prior, every other kind only its latest; a memoryless entity's response answers its own
   request). Each contributes the occurrences it holds.
2. **find** — the finder, once per input, returns candidate occurrences at this payload.
3. **attribute** — one owner per span: (a) a same-type finding inside what reached (by span, or
   as a value inside the held value) is that unit confirmed, not a new origin; (b) nested
   same-type spans go to the longer value; (c) one origin through two carriers is two edges, one
   occurrence; (d) every finding left over is a new unit. Different types on one span are both
   kept — ambiguity is recorded, not resolved.
4. **summarize** — the edges into the leg become its row in `leg_inherited_labels`.

An absent payload is routed and carries nothing (no truncation). An unclassified payload still
carries what reached it; the trace is `partial` until every payload-bearing leg is classified.

## The tables (migration 0024, plus 0025's queue)

| table | one row per | notes |
|---|---|---|
| `payload_label_edges` | edge | `edge_id` = uuid5 of (trace, to leg, origin, from leg, held run, span); `match_kind` declares `whole, normalized, structural, fragment, semantic` (exact writes `whole`); span, `from_trace_id`, `origin_trace_id` nullable |
| `leg_inherited_labels` | payload-bearing leg of a derived trace | inherited tags by kind, highest level, origin and edge counts, `classified`, the `payload_hash` and `matcher` it was derived with |
| `label_lineage_trace_status` | derived trace | `complete` / `partial`, unclassified legs, unanchored findings; `edge_count` counts every edge, the cross-conversation ones included (a leg row's `edge_count` does not) |
| `risk_recompute_requests` | interaction | its inherited summary changed after it may have been decided |
| `label_lineage_dirty_traces` (0025) | trace to derive again | a trace it read as a prior changed; written by the change gate, cleared by the next derivation |

The processor (`python -m data_governance.processors.label_lineage`, `deploy/k8s/93-label-lineage.yaml`)
drains `interaction_legs` readiness-gated on `dg_interaction_leg_ready` (a leg is delivered once its
payload is absent or classified), re-derives each trace of a delivered run once, in the transaction
that advances its `label_lineage` cursor, and deletes the trace's rows the derivation no longer
produces by set membership. A stale arm re-derives traces whose legs changed under the cursor. Every
changed inherited summary (a first row with edges included) is written to `risk_recompute_requests`,
and each derivation notifies `dg_interaction_leg_ready`; the processor reads no risk configuration.

## The flag: `RISK_INHERITED_LABELS`

Set on `85-leg-ready.yaml` only; leg-ready alone decides what inherited labels do.

- `off` (default) — the risk engine never reads the label tables; OPA input, fingerprint and record
  are byte-identical to the engine without label lineage.
- `on` — each flow gets ONE more document-level data item: the union of the tags of the whole
  values that reached that leg from origins in the same trace, the highest level, every matched span
  in `entities[]`. A rule fires on an inherited PII exactly as on a detected one. Leg readiness also
  waits for the leg's `leg_inherited_labels` row at its current payload hash — so a stopped
  label-lineage pod holds the risk stream, by design — and leg-ready drains
  `risk_recompute_requests` through the risk observer. The engine reads only edges at the payload
  each leg carries now. With `off` the requests are left alone (one row per interaction).
- `all` — reserved and rejected at leg-ready's startup: labels that crossed from another conversation are
  lineage information, never decision input (below).

## Across conversations: `LABEL_CROSS_TRACE_WINDOW_HOURS`

Set on `93-label-lineage.yaml`; `0` (the default) is off. With a window of W hours, before a trace is
derived the processor loads its **priors**: for each entity of the trace that keeps state — an agent
(it accumulates, ADR-0028 D2) or a declared source such as a store — every payload-bearing leg of
another trace that entity was handed or said, from W hours before the trace's first leg up to its
last, with the occurrences it holds there (its own findings and what reached it, read back from the
edge table). A prior joins the inputs of the legs that entity emits in this trace, when it was on the
wire before them; each unit once, at the entity's latest holding of it. "Keeps state" is kind-based today
(ADR-0028 D2 and D12): every `agent` and every `tool` — a pass-through tool, the external partner included —
bridges conversations; an LLM or a user never does. An agent is one shared Deployment: two users' conversations through
it inside the window are linked when the same bytes appear in both — the path then runs into another
user's conversation. That is a policy decision for whoever opens the window (narrow the window, or
keep it closed where agents serve several tenants); the processor records what the bytes show.
Re-classifying a payload in place (ADR-0024's runbook) changes what priors read without queueing the
traces that read it — the same blind spot as the stale arm's; the manifest's recovery (truncate the
label tables, reset the cursor) covers both.

What crosses is recorded in the same edge table: the crossing edge names the carrier's trace
(`from_trace_id`), and every edge after it names the unit's trace (`origin_trace_id`). The crossing
is recorded where the value enters this conversation; after that it travels as the conversation
carries it. **It is lineage information for following a value's path — the Label lineage tab walks
it across the boundary — and never inheritance:** a foreign unit never claims a finding of this
conversation (the finding stays this conversation's own origin, the foreign occurrence sits beside
it), never suppresses a shorter own value, and never comes back through another conversation. So
`leg_inherited_labels`, the recompute requests and the OPA input are the same with the window open
or closed; a test proves it on the replayed rows.

When a derivation changes what priors read of a trace (its edges, or a leg's payload or
classification), the traces that may have read it (they share a stateful entity and overlap the
window, the mirror of the priors' bounds) are queued in `label_lineage_dirty_traces` (migration
0025, the only table this adds: `trace_id`, `requested_at`, `reason`) and re-derived by a third arm
of the drain; every derivation deletes its trace's row in its own transaction. A derivation that
changes nothing queues nothing, so two conversations that read each other settle. Opening the window
does not re-derive the traces already derived: priors are read when a trace is derived, so the
crossing edges appear on traces derived from then on (reset the cursor to rebuild the rest).

## Reading it

`GET /api/traces/{tid}/interactions/{iid}/labels` (`retrieval.get_leg_labels`) serves, per
payload-bearing leg, the classifier's findings on the payload (**found here**) beside the edges into
the leg at its current payload hash (**carried in**), each with its origin leg, `seq`, caller ->
callee, `unit_text` / `unit_value`, `match_kind` and `origin_trace_id` (null for this trace; an origin in another
trace is reached through its chain, not linked from the row). `retrieval.labels.merge` pairs them per
(span, entity type): a finding claims the same-type edges whose span overlaps its own (the extent rule
can widen a number past what the classifier marked), an unclaimed edge group is a carried-in row,
and two labels never fold into one. `lineage_status` is null when the trace is not derived (or 0024
has not run), never `complete`. The trace page's Classification tab shows this as facts about the
payload; it never says a label was used, because the API pod does not read `RISK_INHERITED_LABELS`.
The risk layer says "used in this decision" only from the record's
`classification_summary.<leg>.inherited`, which leg-ready writes only for edges it forwarded. Which
rule a label fired is not stored, and no page claims it.

**The lineage of each value (task 5).** Every carried-in origin on a label carries `chains`: the routes
the value took from the payload where the classifier found it to this leg, one hop per payload, in
`seq` order, each hop with `role` (`origin` / `hop` / `here`), its interaction, leg, `seq`, caller ->
callee, the span at that payload (the unit on the origin) and its `trace_id`. The walk follows the
edges' `from_*` back from the leg, each next hop read at the payload the edge was derived from
(`from_payload_hash`), so a chain is a chain of payloads; only the leg read is pinned to its current
payload. A value that reached the leg two ways (the edges form a DAG) has two chains, every complete
route, shortest first. The walk crosses into other traces through `from_trace_id` / `origin_trace_id`
(the exact finder writes them since slice 2, across the window). A cycle stops at the repeated edge, the
depth is capped (`MAX_CHAIN_DEPTH`, logged when hit), and a walk that ends before the origin is kept
as a chain whose first hop is a `hop`, never shown as the origin. The origin hop carries an `excerpt`
of its payload's projected text around the unit.

The UI keeps the Classification tab the classifier's (label lineage there is one more source of labels)
and adds a tab per leg titled **Data lineage** (`LabelLineageTable`): one block per carried-in value with
its **origin** (where the classifier found it) and the **previous** hop(s) at distance 1, each a
link to that interaction, and "Show chains" for the full routes (`LabelLineage`: origin, then each
carrier, then the **current** leg; the first chain open, the rest folded under a count). The
risk layer's decision card opens the same view from its "Carried in" row, under the fired rules'
criteria. A hop links to its interaction on the trace page (`?iid=`), a hop in
another trace is marked and links to that trace's flow view, and a hop under the pointer lights its
interaction on the sequence diagram. The decision is payload-level and lineage is value-level: the panel never names the
value that met a rule, because neither the engine nor OPA records one; the reader sees which value
carries the labels the rules name.

**Tracking values on the graphs (the overlay).** An edge on the graphs is a leg, one payload, and the
decision is on that payload; label lineage is per value. So a value's chain is not "the path of an
edge": the reader ticks the values to track (in the Data lineage tab, or by a right-click on a leg,
which tracks that leg's carried-in values; with the risk layer on the default is the carried-in values whose
labels the fired rules name), and the union of their routes is painted over the graph that is already
there: the legs those values sat on lit, every other leg dimmed, on the execution-flow graph, the
sequence diagram and the Tree and Flat tables alike (`lib/labelTrack`, pure; the pages only paint the
sets). A banner names what is tracked, each removable; hops in other traces are counted, not drawn.
Tracking is per trace and per page visit. With edges per interaction the graphs draw request legs
only, and a value's response-leg hop lights its interaction's one arrow.

## What this does not do yet

- No comparison other than byte equality: a re-spaced number (`123 45 6789`), a re-cased name or a
  spelled-out email is not the unit (structural transformations come next).
- No fragments: a value split across fields or cut to its last four reaches nothing; `recombined`
  is always false.
- Across conversations, only through entities that keep state, only inside the window, and only as
  lineage information: no decision reads a label that crossed (the labels read walks the crossing;
  the trace page marks a hop in another trace and links its flow view). The store bridges two
  conversations (it is a declared source) but not two turns of one (data lineage's D2 keeps a tool
  memoryless).
- No semantic judge. Data lineage keeps its own `SEMANTIC_MATCHER`; label lineage's finders are not offered there.
- No floor on a unit's length: a short finding (a two-digit age, a misread word) is found wherever
  those bytes stand whole and bounded, so its labels travel as far as the bytes do.
- An entity row landing after its trace was derived does not re-trigger the derivation, and re-classifying
  payloads (ADR-0024's runbook) does not either: truncate the label tables with them (the manifest's recovery).
- No cap on the work per trace: a value repeated many times, or a conversation that echoes the same facts
  turn after turn, grows candidates and edges with the product of copies and carriers.
- A labels row both found here and carried in shows the higher of the two levels and the union of
  the tags, and does not say which source gave which. When a same-type edge carries a higher level
  than the classifier's finding on the payload, the row reads as if the classifier found that level
  there. Not seen in live data yet; the fix is to split the row when the levels differ.
- The chains show every hop's matched span but only the origin hop's payload text; a carrier's
  excerpt would cost a payload read per hop and nothing asks for it yet.
- No per-value cause: the engine and OPA decide the payload from its data items and record rule ids,
  not the value that met a rule, so the values panel lists values and their lineage beside the rules'
  criteria and leaves the match to the reader.

## Roadmap

The feature space is a grid of cases — transformation (exact | structural | free) ×
shape (straight | split-merge inside | assembled late) × time (one conversation | across) × content
(identifier | content); this is exact / straight / one conversation, and every later capability is
added beside it, as a finder, an attribution clause or more inputs.
