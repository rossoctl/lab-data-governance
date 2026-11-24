# Label lineage

A classifier finds **units** in payloads (an SSN, a name, a booking reference). Label lineage says,
for every payload of a trace, which earlier units are still present in it, and gives the risk engine
those labels as if the classifier had found them there. It is a second derivation beside data
lineage (ADR-0028, untouched): the same legs and the same classifier output. Its inbound routing
starts from the same function (`traversal.inbound_sets`, D1 and D2) and differs from data lineage's
in three ways: a memoryless entity's response answers its own request, a tool or an LLM inside a call
is routed by that call, and an entity's declared memory scope replaces its kind's (all under
[The fold at one leg](#the-fold-at-one-leg)).

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
- **memory scope** — what an entity remembers: `call` (inside one invocation), `conversation`
  (across its invocations under one root), `persistent` (across conversations). By kind: `llm`,
  `tool`, `user`, `client` call; `agent` conversation; `service` persistent. `LABEL_MEMORY_SCOPES`
  (a JSON object, `{"<natural_key>": "<scope>"}`) declares it per entity; not JSON this parser can
  read (nested too deeply or an integer too long included), not an object, an empty key, an unknown scope or a key given twice
  (at any depth) exits 2. A declared key that matches no entity changes nothing and is not reported.
  Inside one conversation only a declared `call` or `conversation` changes the fold; an entity
  declared `persistent` keeps what its kind does there. With `LABEL_CROSS_CONVERSATION=on` the
  crossing reads `persistent` (below): a value such an entity received, in another conversation or
  in an earlier call of this one, is linked to the payload that sends it again, so a store that
  returns a value later in the same conversation draws a crossing there.
  A changed declaration re-derives nothing by itself: it reaches a conversation when that
  conversation is derived again, so until the replay (the recovery in `deploy/k8s/93-label-lineage.yaml`)
  history is a mix of the old and the new declaration, conversation by conversation.
  `processors/data_lineage/memory.py` holds it; data lineage reads none of it.

## The fold at one leg

Legs are walked in wire order, `(occurred_at, seq)`: `seq` is not causal on the lab traces (the
egress request can carry a lower seq than the store read that fed it). At each payload-bearing leg:

1. **inputs** — the leg's inbound legs (requests reach the callee, responses the caller; agents
   keep every prior, every other kind only its latest; a memoryless entity's response answers its own
   request; an entity declared `conversation` keeps every prior, one declared `call` only its call's). Each contributes the occurrences it holds.
2. **find** — the finder, once per input, returns candidate occurrences at this payload.
3. **attribute** — one owner per span: (a) a same-type finding inside what reached (by span, or
   as a value inside the held value) is that unit confirmed, not a new origin; (b) nested
   same-type spans go to the longer value; (c) one origin through two carriers is two edges, one
   occurrence; (d) every finding left over is a new unit. Different types on one span are both
   kept — ambiguity is recorded, not resolved.
4. **summarize** — the edges into the leg become its row in `leg_inherited_labels`.

An absent payload is routed and carries nothing (no truncation). An unclassified payload still
carries what reached it; the trace is `partial` until every payload-bearing leg is classified.

## The tables (migration 0024)

| table | one row per | notes |
|---|---|---|
| `payload_label_edges` | edge | `edge_id` = uuid5 of (trace, to leg, origin, from leg, held run, span); `match_kind` declares `whole, normalized, structural, fragment, semantic` (exact writes `whole`); span, `from_trace_id`, `origin_trace_id` nullable |
| `leg_inherited_labels` | payload-bearing leg of a derived trace | inherited tags by kind, highest level, origin and edge counts, `classified`, the `payload_hash` and `matcher` it was derived with |
| `label_lineage_trace_status` | derived trace | `complete` / `partial`, unclassified legs, unanchored findings |
| `risk_recompute_requests` | interaction | its inherited summary changed after it may have been decided |

Across conversations (migration 0031, written only with `LABEL_CROSS_CONVERSATION=on`):
`label_occurrences` (what a persistent entity received), `label_crossings` (a crossing's rule level
beside its edge, or a common value's count) and `label_crossed_edges` (per edge whose value rests on a
crossing: its level and whether ambiguous at its to leg).

The processor (`python -m data_governance.processors.label_lineage`, `deploy/k8s/93-label-lineage.yaml`)
drains `interaction_legs` readiness-gated on `dg_interaction_leg_ready` (a leg is delivered once its
payload is absent or classified), re-derives each trace of a delivered run once, in the transaction
that advances its `label_lineage` cursor, and deletes the trace's rows the derivation no longer
produces by set membership. A stale arm re-derives traces whose legs changed under the cursor. Every
changed inherited summary (a first row with edges included) is written to `risk_recompute_requests`,
and each derivation notifies `dg_interaction_leg_ready`. It reads no risk configuration.

## Across conversations: `LABEL_CROSS_CONVERSATION`

Off by default, and it alone governs what this processor does across conversations (whatever the
risk flag says, as the rest of label lineage). The policy reads a crossed label only while leg-ready
has `RISK_INHERITED_LABELS=on` and this switch on (below). Every `LABEL_CROSS_*` setting is read at startup whether the switch is on or off, and an
invalid one exits 2: a label type in `LABEL_CROSS_ANCHORS` that is not in the classifier's tag table
(the TAG column of `processors/classification/_config_data/EntityTypesForTokenClassification_with_tags.csv`),
a retention that reaches past the last date, a limit that is not a positive integer. An anchor name is
checked against that file, not against the label set of the loaded model: a type the model emits and
the file lacks cannot be made an anchor. With it on, at each leg in wire order:

1. **match** — a payload a `persistent` entity sends is searched for the values it received in
   other conversations (and in earlier calls of this one, R7): exact, the whole bounded value, an
   occurrence valid strictly inside `(received, received + LABEL_CROSS_RETENTION_SECONDS)` in event
   time. A value held from more than `LABEL_CROSS_COMMON_LIMIT` (M, default 3) other conversations is
   *common*: no edge, the count is written. The rest pass the rule table, per record of the leg
   that received them: no anchoring identifier (`LABEL_CROSS_ANCHORS`, the person identifiers by
   default) draws nothing; an identifier alone is `value_only`; an identifier and another value of
   the same record are `corroborated`. A record is the object a value is the whole member of in a
   structured payload; a value in prose or inside a string has none and is value-only at most.
2. **crossing edge** — one `payload_label_edges` row per occurrence that passes, from the leg that
   received it (`from_trace_id`) with its origin unit (`origin_trace_id`); its level, or a common
   value's count, is a `label_crossings` row. A crossing never claims a finding of the leg.
3. **carry** — the fold carries the crossed occurrence on to later legs as ordinary edges with that
   origin, and keeps it *crossed* with a level (R3): a crossing's step is the weakest of
   its rule level and the level its holding was registered with; an ordinary edge carries its
   carrier's level; of several paths of one origin the best holds, and an origin known inside the
   conversation is *certain* (it reads `inherited`). Of the crossed occurrences on one span of a leg
   the value carries the strongest level and is *ambiguous* when they are of more than one origin
   unit. Every edge into such a value has a `label_crossed_edges` row (`level`, `ambiguous`); a
   change there alone queues a recompute like a changed summary.
4. **register** — every labelled value a `persistent` entity receives is a `label_occurrences` row
   with its record, interval and evidence (`classified`, `inherited`, or `crossed` with the level it
   arrived with, at any leg after a crossing),
   replaced when the conversation is derived again. A derivation with the switch off writes none
   and deletes the trace's rows; switching on does not look back. A leg without an event time is
   neither matched nor registered. A conversation derived again after the switch was turned on is
   derived whole, so the legs it received before the switch are registered too: "does not look
   back" means no conversation is derived because the switch changed.
   A row keeps the interval it was registered with: a changed `LABEL_CROSS_RETENTION_SECONDS`
   applies to values registered after the change, and to a conversation derived again after it.

A derivation loads only the occurrences of its persistent senders, from other conversations, valid in
its time window, whose value is in a payload they send. A value found bounded
begins and ends at word boundaries of the payload, so its core (first letter or digit to last) is a run
of its own number of consecutive payload words; each occurrence stores the hash of its core and its word
count, and the derivation hashes every run of 1..K words of each payload a sender sends (K the most
words among that sender's occurrences the load could return: other conversations, the same window) and
loads by those hashes. K is read from an index on (entity, `valid_to`) that includes the columns it
filters on, so its scan covers the sender's occurrences still valid at the window's start, not its
history. The cost per payload is its words times K hashes and index probes, whatever the values look
like; a long value that expired, or one held only in this conversation, does not count. The match then costs one lookup per start
boundary and distinct held length. An occurrence row is deleted only when its conversation is derived
again: there is no expiry job, so `label_occurrences` keeps every value a persistent entity received
(the index on `valid_to` keeps expired rows out of the load).

The policy reads a crossed label when leg-ready has `LABEL_CROSS_CONVERSATION=on` too (its value must
equal this processor's): the crossed edges into a leg are one more document-level data item of that
flow, after the in-conversation one and in the same shape, so a rule on the labels fires on them (one
identifier alone blocks under the default policy, R5), and the flow states how surely in its own
field `crossed` = `{levels, ambiguous, count, regulatory_tags, max_level}` (in `opa_input.schema.json`;
no rule field states it yet). The field merges the flow's crossed labels: it does not say which label
has which level. The record's `classification_summary.<leg>.crossed` is the same. A crossing inside
one conversation (R7) is in that item too. With the switch off on leg-ready the engine is exactly as
without it, whatever rows exist; that is why the two Deployments must carry the same value: with label
lineage on and leg-ready off, a crossing inside one conversation still reaches the policy, as an
ordinary carried label of the in-conversation item with no `crossed` field (its origin is in the
trace), while a crossing from another conversation does not reach it.

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
- `all` — rejected at leg-ready's startup: labels from other conversations have their own switch,
  `LABEL_CROSS_CONVERSATION`, set on leg-ready beside this one (above).

## Reading it

`GET /api/traces/{tid}/interactions/{iid}/labels` (`retrieval.get_leg_labels`) serves, per
payload-bearing leg, the classifier's findings on the payload (**found here**) beside the edges into
the leg at its current payload hash (**carried in**), each with its origin leg, `seq`, caller ->
callee, `unit_text` / `unit_value`, `match_kind` and `origin_trace_id` (null for this trace; the
trace page does not link an origin in another one). `retrieval.labels.merge` pairs them per
(span, entity type): a finding claims the same-type edges whose span overlaps its own (the extent rule
can widen a number past what the classifier marked), an unclaimed edge group is a carried-in row,
and two labels never fold into one. `lineage_status` is null when the trace is not derived (or 0024
has not run), never `complete`. The trace page's Classification tab shows this as facts about the
payload; it never says a label was used, because the API pod does not read `RISK_INHERITED_LABELS`.
The risk layer says "used in this decision" only from the record's
`classification_summary.<leg>.inherited`, which leg-ready writes only for edges it forwarded; the risk
panel does not show a crossed label (`classification_summary.<leg>.crossed`) as the cause of a decision
yet. Which
rule a label fired is not stored, and no page claims it.

**The lineage of each value.** Every carried-in origin on a label carries `chains`: the routes
the value took from the payload where the classifier found it to this leg, one hop per payload, in
`seq` order, each hop with `role` (`origin` / `hop` / `here`), its interaction, leg, `seq`, caller ->
callee, the span at that payload (the unit on the origin) and its `trace_id`. The walk follows the
edges' `from_*` back from the leg, each next hop read at the payload the edge was derived from
(`from_payload_hash`), so a chain is a chain of payloads; only the leg read is pinned to its current
payload. A value reaches a leg along many routes (the edges form a DAG with fan-in: an agent keeps
every earlier holding, every LLM round trip echoes the value back), and their number multiplies along
the fan-in while the number of edges does not; so the read never lists them. It reads the sub-graph of
edges behind the leg once (a set union, linear in edges: the edges into each payload are found through
the index on (to leg, payload), migration 0030; through the index on the origin unit, which the planner
would otherwise prefer, every hop reads every edge of the value) and derives one shortest route per edge into
the leg, shortest first, `route_count`, how many routes there are in all (saturating at 2^53 − 1,
JavaScript's largest safe integer: a count at the cap means at least that many), and `carriers`, every
leg on any of them (the origin and this leg included). The walk crosses into
other traces through `from_trace_id` / `origin_trace_id` (a crossing writes them; the read
follows them). A route that cannot reach the origin ends at the nearest edge with no edge of its own
(where the walk meets itself only when every route circles; on such data, a fault, the count and the
carriers are not exact) and is kept as a chain whose first hop is
a `hop`, never shown as the origin. The origin hop carries an
`excerpt` of its payload's projected text around the unit.

The UI keeps the Classification tab the classifier's (label lineage there is one more source of labels)
and adds a tab per leg titled **Data lineage** (`LabelLineageTable`): one block per carried-in value with
its **origin** (where the classifier found it) and the **previous** hop(s) at distance 1, each a
link to that interaction, and "Show chains" for the routes the read lists, one shortest per previous hop
(`LabelLineage`: origin, then each carrier, then the **current** leg; the first chain open, the rest
folded under a count). The
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
labels the fired rules name), and the union of their carriers (every leg on any route, not only the
shortest ones the chains show) is painted over the graph that is already there: the legs those values
sat on lit, every other leg dimmed, on the execution-flow graph, the sequence diagram and the Tree and Flat tables alike (`lib/labelTrack`, pure; the pages only paint the
sets). A banner names what is tracked, each removable; hops in other traces are counted, not drawn.
Tracking is per trace and per page visit. With edges per interaction the graphs draw request legs
only, and a value's response-leg hop lights its interaction's one arrow.

## The crossing rules

A *holding* is one time a persistent entity received a labelled value; a *crossing* is an edge from a
holding of one conversation to a payload the entity sends in another. The system cases that cross
conversations (`tests/system`, K1–K30) cite these rules by number, and so does the code that
implements one (`docs/LIVE-E2E.md` numbers its live scenarios with an R too; those are not these).

- **R1** A holding is valid strictly after the time its value was received and strictly before that
  time plus the retention, in event time: `from < t < to`. A value cannot leave at the instant it
  arrives, and whether it has expired does not depend on rounding.
- **R2** A value held from more than M other conversations is a *common value*: it draws no
  crossing, only a count on the leg. The count is taken when the payload is matched, over the
  holdings valid at the payload's time, by distinct conversation (three holdings in one conversation
  count once); a stored count is only a cache.
- **R3** A value with several origins carries the strongest of their levels and is marked
  *ambiguous*; along one path the weakest step holds.
- **R4** A value's record is the innermost object of a structured payload that holds the value as
  the whole of one member. A payload with no structure, or a value inside a longer string, has no
  record: its crossings are value-only at most.
- **R5** One anchoring identifier alone is a value-only crossing, and its label reaches the policy
  like any other: lineage states the level, the policy decides, and a rule that blocks the label
  (the suite's T-SENS does) blocks it. What is uncertain is which earlier conversation the value came
  from, and when several gave it, *ambiguous* says so.
- **R6** Holdings are written only while the cross-conversation switch is on. Switching it on does
  not look back.
- **R7** When a persistent entity returns, in a later call of the same conversation, a value it
  received earlier in it, the crossing mechanism draws that edge, once. Inside one call the
  conversation's own edge stands alone.
- **R8** Whether a value anchors follows the label type the classifier gave it at the leg where the
  entity received it; a case states that type.
- **R9** The defaults, each a setting: M = 3, and a retention of 15 minutes.
- **R10** A persistent entity needs nothing beyond its scope (its kind's, or declared) to cross: a
  shared agent mixes its users by definition, and declaring it is the acceptance.

All ten are built, in `label_lineage/cross.py` and the fold (R5 through leg-ready, with
`RISK_INHERITED_LABELS` and `LABEL_CROSS_CONVERSATION` on). Not yet: a conversation derived before a
value it would cross arrived is not derived again, so R1 and R2 hold at the payload's time only when
conversations are derived in event order.
Nor is a conversation derived again when the interactions of a conversation it crossed from move to
another trace: its crossing edges name the old trace (`from_trace_id`) and its chain ends at the
crossing, until it is derived again.

## What this does not do yet

- No comparison other than byte equality: a re-spaced number (`123 45 6789`), a re-cased name or a
  spelled-out email is not the unit (structural transformations come next).
- No fragments: a value split across fields or cut to its last four reaches nothing; `recombined`
  is always false.
- A conversation derived before an earlier one arrived is not matched again (comes next). The
  verdict does not yet ask in tiers (classification, in-conversation, cross-conversation).
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
(identifier | content); this is exact / straight / one conversation and across conversations through a
persistent entity, and every later capability is
added beside it, as a finder, an attribution clause or more inputs.
