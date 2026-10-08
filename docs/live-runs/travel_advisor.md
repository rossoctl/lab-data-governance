# Live run 2026-10-08T060615Z-f0d2808

cluster `kind-rossoctl` · app `travel-advisor` · ns `travel-advisor` · DG `f0d2808` · destructive True

**Scenarios**: 14 passed · 1 skipped  
**Rows**: 478 — 378 pass · 0 FAIL · 4 KNOWN · 96 recorded  
**Preflight**: 96 pins, mismatches: none  
**Capabilities**: alerts=False health=False ledger=True  
**Catalog**: test catalog proved live by ['E2E-INT-DATA', 'E2E-PI-INT']; shipped catalog restored and proved; `catalog-after.yaml` present.

| scenario | outcome | s | reason / failure |
|---|---|---:|---|
| L1_baseline_turn | passed | 104.6 |  |
| L2_ledger_matches_the_tables | passed | 1.1 |  |
| L3_abandoned_exchange | passed | 60.5 |  |
| L4_no_payload_interaction | passed | 15.5 |  |
| L5_simultaneous_legs | passed | 19.9 |  |
| L6_cross_trace_isolation | passed | 135.6 |  |
| L7_known_payloads_to_the_psp | passed | 31.3 |  |
| L8_mixed_levels_in_one_trace | passed | 18.4 |  |
| L9_incremental_legs_reversion | passed | 0.1 |  |
| L10_idempotency | passed | 20.0 |  |
| L11_response_only_pii_is_not_sent | passed | 19.7 |  |
| L12_opa_outage_holds_the_cursor | passed | 34.4 |  |
| L13_alert_supersession | skipped | 0.0 | no alerts processor on the deployed branch (capabilities.alerts=False) |
| L14_rogue_partner_notification | passed | 74.5 |  |
| L15_cut_stream_is_not_ok | passed | 81.3 |  |

**Known findings reproduced**: risk record keeps the stale peer.host callee (#279)

## L1_baseline_turn — passed (104.6 s)

> L1 — one scripted turn under a minted trace id, and nothing else (#161 scenario 1).

41 rows: 36 pass · 0 FAIL · 1 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.answer` | the turn answered | non-empty | 1668 chars | pass |  |
| `settle` | settled |  | 17.3 s · 9 polls · 82 spans | recorded | absent streams ['risk_alerts'] |
| `forest.interactions` | interactions derived |  | 28 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 28 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 13} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 28 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 10, "2": 13, "3": 1, "4": 3} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 13} | recorded |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 5 content-kind violations | KNOWN | soundness; known gap content-kind-collision |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 3 deterministic | 3 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a", "inference"] | ["a2a", "inference", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 1 | 4 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 3 | 3 | pass | completeness |
| `audit.F1` | the entry request payload carries the text the driver sent | ["Maya Park", "acct_001"] | {"captured_chars": 506, "missing": []} | pass | fidelity |
| `audit.F2` | the entry response payload is the reply the driver received | Your trip to Japan has been successfully booked! Here are the details:  - **Trip | Your trip to Japan has been successfully booked! Here are the details:  - **Trip | pass | fidelity |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 12s changes no lineage row | {"attached": 82, "interactions": 28, "legs": 56, "shape": "abc94bca-e6e5-5cb9-9262-02b371bb87f3", "spans": 82} | {"attached": 82, "interactions": 28, "legs": 56, "shape": "abc94bca-e6e5-5cb9-9262-02b371bb87f3", "spans": 82} | pass | invariance |

## L2_ledger_matches_the_tables — passed (1.1 s)

> L2 — the app's own ledger against the derived tables, on L1's trace (Tier C), before any probe is added to it.

16 rows: 7 pass · 0 FAIL · 0 KNOWN · 9 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 14, "out": 28, "pairs": 40, "records": 122, "spans": 41} | pass | pods without a record in the window: ['demo-client', 'notify-partner', 'send-notification'] (is ROSSOCTL_LEDGER=stdou… |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 40 | recorded | out 28 in 14 of 41 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 18 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.dark_hops` | store records under the trace |  | 4 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 2 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 5 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'create-booking', 'payment-agent', 'research-agent', 'travel-advisor'] / ['booking-agent', 'create-… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "notify-partner", "send-notification"] | recorded |  |

## L3_abandoned_exchange — passed (60.5 s)

> L3 — an exchange the far end never answers (#161 scenarios 4 and 6, the lineage half; the record's versions are L9's).

21 rows: 17 pass · 0 FAIL · 0 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the client gave up on the sink | client_timeout | client_timeout | pass |  |
| `probe.held` | the sink never answered (client held ≥ 44 s) | ≥ 44 s | 45.5 s | pass |  |
| `settle` | settled |  | 14.8 s · 8 polls · 84 spans | recorded | absent streams ['risk_alerts'] |
| `span.present` | a response span exists for the abandoned exchange | present | present | pass |  |
| `span.outcome` | response span lineage.outcome | abandoned | abandoned | pass |  |
| `span.status` | response span http.status_code |  |  | pass |  |
| `legs.response.error` | response leg error flag | true | true | pass |  |
| `legs.response.payload` | response leg payload hash |  |  | pass |  |
| `legs.request.payload` | request leg has a payload | present | present | pass |  |
| `forest.interactions` | interactions derived |  | 29 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 29 | pass |  |
| `forest.roots` | roots = injected entries | 2 | 2 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 2 | 2 | pass | inbound parent sources {'tracestate': 13} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 2 | 29 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 2, "1": 10, "2": 13, "3": 1, "4": 3} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 13} | recorded |  |

## L4_no_payload_interaction — passed (15.5 s)

> L4 — an exchange with no body either way (#161 scenario 8, the lineage half; its record is read in L7).

16 rows: 12 pass · 0 FAIL · 0 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the GET was answered (404 is fine) | ["ok", "http_error"] | http_error | pass |  |
| `settle` | settled |  | 14.8 s · 8 polls · 86 spans | recorded | absent streams ['risk_alerts'] |
| `legs.request.payload` | request leg payload hash |  |  | pass |  |
| `legs.response.payload` | response leg payload hash |  |  | pass |  |
| `forest.interactions` | interactions derived |  | 30 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 30 | pass |  |
| `forest.roots` | roots = injected entries | 3 | 3 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 3 | 3 | pass | inbound parent sources {'tracestate': 13} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 3 | 30 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 3, "1": 10, "2": 13, "3": 1, "4": 3} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 13} | recorded |  |

## L5_simultaneous_legs — passed (19.9 s)

> L5 — simultaneous legs inside one trace (#161 scenarios 2, 3).

15 rows: 10 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | the four probes' outcomes |  | {"card_ext": "ok", "cred_int": "ok", "nobody_get": "http_error", "pi_int": "ok"} | recorded |  |
| `settle` | settled |  | 19.1 s · 10 polls · 94 spans | recorded | absent streams ['risk_alerts'] |
| `probes.interactions` | four simultaneous probes derive four distinct interactions | 4 | 4 | pass |  |
| `forest.interactions` | interactions derived |  | 34 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 34 | pass |  |
| `forest.roots` | roots = injected entries | 7 | 7 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 7 | 7 | pass | inbound parent sources {'tracestate': 13} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 7 | 34 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 7, "1": 10, "2": 13, "3": 1, "4": 3} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 13} | recorded |  |

## L6_cross_trace_isolation — passed (135.6 s)

> L6 — two users at once stay two traces (#161 scenario 5).

117 rows: 89 pass · 0 FAIL · 2 KNOWN · 26 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.maya.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.maya.answer` | the turn answered | non-empty | 492 chars | pass |  |
| `turn.noa.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.noa.answer` | the turn answered | non-empty | 995 chars | pass |  |
| `settle-6ce59143` | settled |  | 15.0 s · 8 polls · 102 spans | recorded | absent streams ['risk_alerts'] |
| `settle-ed046bcf` | settled |  | 12.9 s · 7 polls · 80 spans | recorded | absent streams ['risk_alerts'] |
| `forest-6ce59143.interactions` | interactions derived |  | 34 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 34 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 17} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 34 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 10, "2": 18, "3": 2, "4": 3} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 17} | recorded |  |
| `audit-6ce59143.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit-6ce59143.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit-6ce59143.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit-6ce59143.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit-6ce59143.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit-6ce59143.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 5 content-kind violations | KNOWN | soundness; known gap content-kind-collision |
| `audit-6ce59143.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit-6ce59143.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit-6ce59143.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit-6ce59143.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit-6ce59143.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit-6ce59143.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit-6ce59143.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit-6ce59143.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit-6ce59143.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 3 deterministic | 3 | pass | completeness |
| `audit-6ce59143.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit-6ce59143.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a", "inference"] | ["a2a", "inference", "mcp"] | pass | completeness |
| `audit-6ce59143.C4` | the tree is as deep as the topology (longest declared path) | 1 | 4 | pass | completeness |
| `audit-6ce59143.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit-6ce59143.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit-6ce59143.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit-6ce59143.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 3 | 3 | pass | completeness |
| `audit-6ce59143.F1` | the entry request payload carries the text the driver sent | ["Maya Park"] | {"captured_chars": 506, "missing": []} | pass | fidelity |
| `audit-6ce59143.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit-6ce59143.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 102, "interactions": 34, "legs": 68, "shape": "f2290b38-89da-590b-a135-807b42d9264d", "spans": 102} | {"attached": 102, "interactions": 34, "legs": 68, "shape": "f2290b38-89da-590b-a135-807b42d9264d", "spans": 102} | pass | invariance |
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 18, "out": 34, "pairs": 50, "records": 378, "spans": 51} | pass | pods without a record in the window: ['demo-client', 'send-notification'] (is ROSSOCTL_LEDGER=stdout on the fleet?) |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 50 | recorded | out 34 in 18 of 51 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 19 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.dark_hops` | store records under the trace |  | 4 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 3 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 14 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'create-booking', 'notify-partner', 'payment-agent', 'research-agent', 'travel-advisor'] / ['bookin… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "send-notification"] | recorded |  |
| `isolation-6ce59143.own` | own guest (Maya Park) captured in this trace | ≥ 1 hop | 31 hops | pass |  |
| `isolation-6ce59143.leak` | the other turn's guest (Noa Levi) in this trace's payloads | [] | [] | pass |  |
| `forest-ed046bcf.interactions` | interactions derived |  | 27 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 27 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 13} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 27 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 10, "2": 13, "3": 1, "4": 2} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 13} | recorded |  |
| `audit-ed046bcf.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit-ed046bcf.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit-ed046bcf.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit-ed046bcf.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit-ed046bcf.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit-ed046bcf.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 5 content-kind violations | KNOWN | soundness; known gap content-kind-collision |
| `audit-ed046bcf.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit-ed046bcf.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit-ed046bcf.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit-ed046bcf.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit-ed046bcf.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit-ed046bcf.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit-ed046bcf.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit-ed046bcf.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit-ed046bcf.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 3 deterministic | 3 | pass | completeness |
| `audit-ed046bcf.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit-ed046bcf.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a", "inference"] | ["a2a", "inference", "mcp"] | pass | completeness |
| `audit-ed046bcf.C4` | the tree is as deep as the topology (longest declared path) | 1 | 4 | pass | completeness |
| `audit-ed046bcf.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit-ed046bcf.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit-ed046bcf.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit-ed046bcf.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 3 | 3 | pass | completeness |
| `audit-ed046bcf.F1` | the entry request payload carries the text the driver sent | ["Noa Levi"] | {"captured_chars": 505, "missing": []} | pass | fidelity |
| `audit-ed046bcf.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit-ed046bcf.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 80, "interactions": 27, "legs": 54, "shape": "cd4992a5-c6c3-5781-bb8d-fe402bc93edd", "spans": 80} | {"attached": 80, "interactions": 27, "legs": 54, "shape": "cd4992a5-c6c3-5781-bb8d-fe402bc93edd", "spans": 80} | pass | invariance |
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 14, "out": 27, "pairs": 39, "records": 378, "spans": 40} | pass | pods without a record in the window: ['demo-client', 'send-notification'] (is ROSSOCTL_LEDGER=stdout on the fleet?) |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 39 | recorded | out 27 in 14 of 40 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 17 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.dark_hops` | store records under the trace |  | 4 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 2 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 14 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'create-booking', 'payment-agent', 'research-agent', 'travel-advisor'] / ['booking-agent', 'create-… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "send-notification"] | recorded |  |
| `isolation-ed046bcf.own` | own guest (Noa Levi) captured in this trace | ≥ 1 hop | 27 hops | pass |  |
| `isolation-ed046bcf.leak` | the other turn's guest (Maya Park) in this trace's payloads | [] | [] | pass |  |
| `isolation.shared_record_ids` | risk record ids shared by the two traces | [] | [] | pass |  |

## L7_known_payloads_to_the_psp — passed (31.3 s)

> L7 — the first read of the risk tables: known payloads to the PSP inside L1's trace (#161 scenarios 9, 10, 12, 13), and the risk shape of everything group A left in T.

56 rows: 48 pass · 0 FAIL · 0 KNOWN · 8 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | the three probes were answered | ["ok¦http_error", "ok", "ok"] | ["ok", "ok", "ok"] | pass |  |
| `settle` | settled |  | 16.9 s · 9 polls · 100 spans | recorded | absent streams ['risk_alerts'] |
| `benign.findings` | benign request leg findings | 0 | 0 | pass |  |
| `benign.level` | benign request leg sensitivity level | PUBLIC | PUBLIC | pass |  |
| `benign.verdict` | benign record verdict | none/allow | none/allow | pass |  |
| `ext.tags` | card ext: request leg regulatory tags | ["PCI", "PI", "PII"] | ["PCI", "PI", "PII"] | pass |  |
| `ext.level` | card ext: request leg sensitivity level | RESTRICTED | RESTRICTED | pass |  |
| `ext.bundle` | card ext: identity bundle | true | true | pass |  |
| `ext.legs` | card ext: legs evidenced | ["request", "response"] | ["request", "response"] | pass |  |
| `ext.response_summary` | card ext: response summary (plain JSON is not parser-captured) | {"payload": null} | {"payload": null} | pass |  |
| `int.tags` | card int: request leg regulatory tags | ["PCI", "PI", "PII"] | ["PCI", "PI", "PII"] | pass |  |
| `int.level` | card int: request leg sensitivity level | RESTRICTED | RESTRICTED | pass |  |
| `int.bundle` | card int: identity bundle | true | true | pass |  |
| `int.legs` | card int: legs evidenced | ["request", "response"] | ["request", "response"] | pass |  |
| `int.response_summary` | card int: response summary (plain JSON is not parser-captured) | {"payload": null} | {"payload": null} | pass |  |
| `ext.verdict` | card external: verdict | critical/block | critical/block | pass |  |
| `ext.rules` | card external: triggered rules | ["DG-001", "DG-004", "E2E-INVERT"] | ["DG-001", "DG-004", "E2E-INVERT"] | pass |  |
| `ext.allowed_actions` | card external: allowed actions | ["redact"] | ["redact"] | pass |  |
| `ext.confidence` | card external: confidence | 0.95 | 0.95 | pass |  |
| `ext.explanation` | card external: explanation = DG-001's alone | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | pass |  |
| `int.verdict` | card internal: verdict | none/allow | none/allow | pass |  |
| `int.rules` | card internal: real rules | [] | [] | pass |  |
| `nobody.summary` | L4's no-body exchange: classification summary | {"request": {"payload": null}, "response": {"payload": null}} | {"request": {"payload": null}, "response": {"payload": null}} | pass |  |
| `nobody.verdict` | L4's no-body exchange: verdict | none/allow | none/allow | pass |  |
| `trace.rollup` | trace rollup | critical/block | critical/block | pass |  |
| `trace.contributing` | both card records contribute to the rollup | both present | both present | pass |  |
| `forest.interactions` | interactions derived |  | 37 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 37 | pass |  |
| `forest.roots` | roots = injected entries | 10 | 10 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 10 | 10 | pass | inbound parent sources {'tracestate': 13} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 10 | 37 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 10, "1": 10, "2": 13, "3": 1, "4": 3} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 13} | recorded |  |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 37 records | 37 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'1d58de685ff454ef235896dd858fe139'} | {'1d58de685ff454ef235896dd858fe139'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 37 | 37 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 37 ids | 37 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 8 | recorded |  |
| `risk.trace_verdict` | trace record |  | v65 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 11 entities | 11 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `turn.finding_counts` | finding counts across the turn's legs |  | [0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6,… | recorded |  |
| `turn.multi_version` | interactions of T with ≥ 2 record versions from natural leg timing |  | 20 | recorded |  |

## L8_mixed_levels_in_one_trace — passed (18.4 s)

> L8 — five probes fired at once into a fresh trace, under the test catalog (#161 scenarios 11, 13, 14).

58 rows: 52 pass · 0 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | the five probes were answered | ["ok", "ok", "ok", "ok", "ok"] | ["ok", "ok", "ok", "ok", "ok"] | pass |  |
| `settle` | settled |  | 17.3 s · 9 polls · 10 spans | recorded | absent streams ['risk_alerts'] |
| `card_ext.verdict` | card_ext: verdict | critical/block | critical/block | pass |  |
| `card_ext.rules` | card_ext: triggered rules | ["DG-001", "DG-004", "E2E-INVERT"] | ["DG-001", "DG-004", "E2E-INVERT"] | pass |  |
| `card_ext.allowed_actions` | card_ext: allowed actions | ["redact"] | ["redact"] | pass |  |
| `card_ext.confidence` | card_ext: confidence | 0.95 | 0.95 | pass |  |
| `card_ext.explanation` | card_ext: explanation | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | pass |  |
| `card_int.verdict` | card_int: verdict | none/allow | none/allow | pass |  |
| `card_int.rules` | card_int: triggered rules | [] | [] | pass |  |
| `card_int.allowed_actions` | card_int: allowed actions | [] | [] | pass |  |
| `card_int.confidence` | card_int: confidence | 1.0 | 1.0 | pass |  |
| `card_int.explanation` | card_int: explanation | No rules fired, falling back to default rule | No rules fired, falling back to default rule | pass |  |
| `pi_int.verdict` | pi_int: verdict | medium/escalate | medium/escalate | pass |  |
| `pi_int.rules` | pi_int: triggered rules | ["E2E-INT-DATA", "E2E-PI-INT"] | ["E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `pi_int.allowed_actions` | pi_int: allowed actions | ["log"] | ["log"] | pass |  |
| `pi_int.confidence` | pi_int: confidence | 0.3 | 0.3 | pass |  |
| `pi_int.explanation` | pi_int: explanation | E2E: personal information on an internal hop.; E2E: INTERNAL-level payload shared internally. | E2E: personal information on an internal hop.; E2E: INTERNAL-level payload shared internally. | pass |  |
| `cred_int.verdict` | cred_int: verdict | high/require_approval | high/require_approval | pass |  |
| `cred_int.rules` | cred_int: triggered rules | ["E2E-CRED-INT"] | ["E2E-CRED-INT"] | pass |  |
| `cred_int.allowed_actions` | cred_int: allowed actions | ["audit"] | ["audit"] | pass |  |
| `cred_int.confidence` | cred_int: confidence | 0.7 | 0.7 | pass |  |
| `cred_int.explanation` | cred_int: explanation | E2E: credentials moved between internal workloads. | E2E: credentials moved between internal workloads. | pass |  |
| `cred_ext.verdict` | cred_ext: verdict | critical/block | critical/block | pass |  |
| `cred_ext.rules` | cred_ext: triggered rules | ["DG-004", "E2E-CRED-EXT"] | ["DG-004", "E2E-CRED-EXT"] | pass |  |
| `cred_ext.allowed_actions` | cred_ext: allowed actions | [] | [] | pass |  |
| `cred_ext.confidence` | cred_ext: confidence | 0.95 | 0.95 | pass |  |
| `cred_ext.explanation` | cred_ext: explanation | RESTRICTED document detected in external sharing event to UNTRUSTED_EXTERNAL destination. Sharing prohibited. | RESTRICTED document detected in external sharing event to UNTRUSTED_EXTERNAL destination. Sharing prohibited. | pass |  |
| `probes.interactions` | five probes, five interactions | 5 | 5 | pass |  |
| `probes.distinct_pairs` | distinct (risk, enforcement) pairs in one trace | ≥ 3 | [["critical", "block"], ["high", "require_approval"], ["medium", "escalate"], ["none", "allow"]] | pass |  |
| `trace.rollup` | trace rollup | critical/block | critical/block | pass |  |
| `trace.rules_union` | trace rules ⊇ the union of the five probes' rules | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `forest.interactions` | interactions derived |  | 5 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 5 | pass |  |
| `forest.roots` | roots = injected entries | 5 | 5 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 5 | 5 | pass | inbound parent sources {} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.probe_only` | a probe-only trace is exactly its roots | 5 | 5 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 5} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {} | recorded |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 5 records | 5 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'dc2d556dae3ca47b58126750771a37c6'} | {'dc2d556dae3ca47b58126750771a37c6'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 5 | 5 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 5 ids | 5 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v5 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 3 entities | 3 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L9_incremental_legs_reversion — passed (0.1 s)

> L9 — a leg that arrives late re-versions the record (#161 scenario 4, the risk half of L3's exchange).

7 rows: 6 pass · 0 FAIL · 0 KNOWN · 1 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `versions.count` | record versions (the late leg re-versioned it) | ≥ 2 | 2 | pass |  |
| `versions.first_legs` | version 1 legs evidenced | ["request"] | ["request"] | pass |  |
| `versions.last_legs` | last version legs evidenced | ["request", "response"] | ["request", "response"] | pass |  |
| `versions.verdicts` | every version's verdict (from the request leg alone) | critical/block | ["critical/block"] | pass |  |
| `decisions.fingerprints` | distinct evidence fingerprints (OPA re-consulted) | ≥ 2 | 2 | pass |  |
| `record.response_summary` | record response summary | {"payload": null} | {"payload": null} | pass |  |
| `turn.multi_version` | interactions of T with ≥ 2 versions from natural leg timing |  | 20 | recorded |  |

## L10_idempotency — passed (20.0 s)

> L10 — unchanged evidence writes no new version (FR-DAS-014).

26 rows: 23 pass · 0 FAIL · 0 KNOWN · 3 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the probe was answered | ok | ok | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 102 spans | recorded | absent streams ['risk_alerts'] |
| `old.history_untouched` | L7's internal record history unchanged | 1 | 1 | pass |  |
| `old.fingerprints_untouched` | L7's internal decision fingerprints unchanged | 1 | 1 | pass |  |
| `new.version` | the new interaction's record version | 1 | 1 | pass |  |
| `new.risk_level` | new interaction's risk_level equals L7's | none | none | pass |  |
| `new.enforcement_type` | new interaction's enforcement_type equals L7's | allow | allow | pass |  |
| `new.rules` | new interaction's rules equals L7's | [] | [] | pass |  |
| `new.summary` | new interaction's summary equals L7's | {"request": {"contains_identity_bundle": true, "finding_count": 6, "is_personalized": true, "model_version": 2, "prim… | {"request": {"contains_identity_bundle": true, "finding_count": 6, "is_personalized": true, "model_version": 2, "prim… | pass |  |
| `trace.no_drift` | interactions of T re-versioned without new evidence | {} | {} | pass |  |
| `trace.contributing` | the new record contributes to the rollup | present | present | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 38 records | 38 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'1d58de685ff454ef235896dd858fe139'} | {'1d58de685ff454ef235896dd858fe139'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 38 | 38 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 38 ids | 38 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 8 | recorded |  |
| `risk.trace_verdict` | trace record |  | v66 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 11 entities | 11 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L11_response_only_pii_is_not_sent — passed (19.7 s)

> L11 — data that only comes BACK from an external host was not sent to it (#271, fixed by #274: one flow per leg).

22 rows: 19 pass · 0 FAIL · 0 KNOWN · 3 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the reflector answered 200 | ok/200 | ok/200 | pass |  |
| `settle` | settled |  | 19.0 s · 10 polls · 104 spans | recorded | absent streams ['risk_alerts'] |
| `legs.payloads` | both legs carry a payload | both | both | pass |  |
| `request.findings` | request leg findings (benign arguments) | 0 | 0 | pass |  |
| `response.pii` | response leg classifies PII | PII in tags, findings > 0 | {"findings": 5, "tags": ["PI", "PII"]} | pass |  |
| `record.no_external_pii_rule` | external-sharing PII rules fired (#271: response-only PII is not sent) | [] | [] | pass |  |
| `record.verdict` | record verdict | none/allow | none/allow | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 39 records | 39 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'1d58de685ff454ef235896dd858fe139'} | {'1d58de685ff454ef235896dd858fe139'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 39 | 39 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 39 ids | 39 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 8 | recorded |  |
| `risk.trace_verdict` | trace record |  | v67 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 12 entities | 12 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L12_opa_outage_holds_the_cursor — passed (34.4 s)

> L12 — OPA unreachable: the leg stream holds, nothing is skipped, catch-up writes exactly one version (#158 acceptance). Opt-in with E2E_DESTRUCTIVE=1: it scales the OPA deployment to zero, a shared component of whatever cluster the tier runs against.

23 rows: 20 pass · 0 FAIL · 0 KNOWN · 3 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the probe was answered while OPA was down | ok | ok | pass |  |
| `outage.backlog_held` | leg-ready backlog across three polls (held, not skipped) | > 0 each | [130, 130, 130] | pass |  |
| `outage.derived` | the probe's interaction derived while OPA was down | present | present | pass |  |
| `outage.no_record` | risk records written with OPA unreachable | 0 | 0 | pass |  |
| `settle` | settled |  | 12.9 s · 7 polls · 106 spans | recorded | absent streams ['risk_alerts'] |
| `catchup.versions` | versions after catch-up | 1 | 1 | pass |  |
| `catchup.verdict` | verdict after catch-up | critical/block | critical/block | pass |  |
| `catchup.no_duplicates` | interactions re-versioned by the catch-up without new evidence | {} | {} | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 40 records | 40 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'1d58de685ff454ef235896dd858fe139'} | {'1d58de685ff454ef235896dd858fe139'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 40 | 40 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 40 ids | 40 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 8 | recorded |  |
| `risk.trace_verdict` | trace record |  | v68 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 12 entities | 12 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L13_alert_supersession — skipped (0.0 s)

> L13 — alert lifecycle over a fresh trace (#104 acceptance). Skips unless the deployed branch runs the alerts processor. A fresh trace because the assertions are a sequence: T is already critical, so a medium head superseded by critical could never be observed there.

_skipped: no alerts processor on the deployed branch (capabilities.alerts=False)_

_no rows recorded_

## L14_rogue_partner_notification — passed (74.5 s)

> L14 — #258, the risk demo: the booking agent's instructions decide what crosses to the travel partner. Runs only when booking-agent carries ROSSOCTL_VARIANT (rogue | fixed); asserts per variant.

11 rows: 6 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.answer` | the turn answered | non-empty | 1069 chars | pass |  |
| `settle` | settled |  | 15.0 s · 8 polls · 102 spans | recorded | absent streams ['risk_alerts'] |
| `booking.statuses` | create_booking statuses in the turn (the notification triggers on confirmed) |  | ["over_budget", "confirmed"] | recorded |  |
| `variant` | booking-agent variant |  | rogue | recorded |  |
| `partner.exchanges` | notify-partner → partner exchanges in the window |  | 1 | recorded | in the turn's trace |
| `partner.fields` | the arguments that reached the partner |  | ["booking_id", "dates", "guest_name", "note"] | recorded |  |
| `partner.name` | rogue: the guest's name is among the arguments | guest_name sent | ["booking_id", "dates", "guest_name", "note"] | pass |  |
| `partner.pii` | rogue: the partner request carries PII | PII in tags | ["PI", "PII"] | pass |  |
| `partner.verdict` | rogue: the partner record verdict | critical/block | critical/block | pass |  |
| `partner.rule` | rogue: DG-001 fired | DG-001 in rules | ["DG-001", "DG-004"] | pass |  |

## L15_cut_stream_is_not_ok — passed (81.3 s)

> L15 — a caller that walks away mid-turn.

49 rows: 33 pass · 0 FAIL · 1 KNOWN · 15 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `cut.bytes_before` | bytes the client read before closing (the turn must still be running) | 0 | 0 | pass | held 20.02 s |
| `settle` | settled |  | 59.3 s · 29 polls · 102 spans | recorded | absent streams ['risk_alerts'] |
| `span.present` | the entry exchange has a response span after settling | present | present | pass |  |
| `span.not_ok` | the cut stream does not read ok | not ok (abandoned, or error with no 2xx) | outcome 'abandoned' status None | pass |  |
| `legs.response.error` | the entry response leg error flag | true | true | pass |  |
| `record.response_summary` | the entry record's response summary | {"payload": null} | {"payload": null} | pass |  |
| `forest.interactions` | interactions derived |  | 34 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 34 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 17} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 34 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 10, "2": 18, "3": 2, "4": 3} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 17} | recorded |  |
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 18, "out": 34, "pairs": 50, "records": 663, "spans": 51} | pass | pods without a record in the window: ['demo-client', 'send-notification'] (is ROSSOCTL_LEDGER=stdout on the fleet?) |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 50 | recorded | out 34 in 18 of 51 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 19 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.dark_hops` | store records under the trace |  | 3 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 3 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 24 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'create-booking', 'notify-partner', 'payment-agent', 'research-agent', 'travel-advisor'] / ['bookin… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "send-notification"] | recorded |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 34 records | 34 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'c641bb0f085be672bb545620ce6845da'} | {'c641bb0f085be672bb545620ce6845da'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 34 | 34 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 34 ids | 34 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 3 | recorded |  |
| `risk.trace_verdict` | trace record |  | v56 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 9 entities | 10 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['fb5ad4d1-0168-5d54-8661-3579e0e349da'] missin… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

