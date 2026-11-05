# Live run 2026-10-13T105742Z-3f72a25

cluster `kind-rossoctl` · app `travel-advisor` · ns `travel-advisor` · DG `3f72a25` · destructive True

**Scenarios**: 15 passed · 1 skipped · 1 xfail  
**Rows**: 517 — 410 pass · 0 FAIL · 5 KNOWN · 102 recorded  
**Preflight**: 99 pins, mismatches: none  
**Capabilities**: alerts=False health=False ledger=True consumer_lock=True  
**Catalog**: test catalog proved live by ['E2E-INT-DATA', 'E2E-PI-INT']; shipped catalog restored and proved; `catalog-after.yaml` present.

| scenario | outcome | s | reason / failure |
|---|---|---:|---|
| L1_baseline_turn | passed | 95.9 |  |
| L2_ledger_matches_the_tables | passed | 1.2 |  |
| L3_abandoned_exchange | passed | 64.8 |  |
| L4_no_payload_interaction | passed | 19.6 |  |
| L5_simultaneous_legs | passed | 19.8 |  |
| L6_cross_trace_isolation | passed | 134.6 |  |
| L7_known_payloads_to_the_psp | passed | 28.5 |  |
| L8_mixed_levels_in_one_trace | passed | 16.2 |  |
| L9_incremental_legs_reversion | passed | 0.0 |  |
| L10_idempotency | passed | 15.8 |  |
| L11_response_only_pii_is_not_sent | passed | 19.8 |  |
| L12_opa_outage_holds_the_cursor | passed | 33.5 |  |
| L16_two_leg_ready_consumers | passed | 31.3 |  |
| L17_truncated_host_is_classified | passed | 15.6 |  |
| L13_alert_supersession | skipped | 0.0 | no alerts processor on the deployed branch (capabilities.alerts=False) |
| L14_rogue_partner_notification | xfail | 69.8 | the booking agent did not call notify_partner this turn (rogue; booking statuses ['over_budget', 'confirmed']): the m… |
| L15_cut_stream_is_not_ok | passed | 65.1 |  |

**Known findings reproduced**: risk record keeps the stale peer.host callee (#279)

## L1_baseline_turn — passed (95.9 s)

> L1 — one scripted turn under a minted trace id, and nothing else (#161 scenario 1).

41 rows: 37 pass · 0 FAIL · 0 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.answer` | the turn answered | non-empty | 386 chars | pass |  |
| `settle` | settled |  | 19.3 s · 10 polls · 108 spans | recorded | absent streams ['risk_alerts'] |
| `forest.interactions` | interactions derived |  | 36 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 36 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 18} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 36 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 13, "2": 15, "3": 1, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 18} | recorded |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (each leg carries its own kind) | as classified | 0 content-kind violations | pass | soundness |
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
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a", "inference"] | ["a2a", "http", "inference", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 1 | 5 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 3 | 3 | pass | completeness |
| `audit.F1` | the entry request payload carries the text the driver sent | ["Maya Park", "acct_001"] | {"captured_chars": 506, "missing": []} | pass | fidelity |
| `audit.F2` | the entry response payload is the reply the driver received | Your trip to Japan has been successfully booked! Here are the details:  - **Trip | Your trip to Japan has been successfully booked! Here are the details:  - **Trip | pass | fidelity |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 12s changes no lineage row | {"attached": 108, "interactions": 36, "legs": 72, "shape": "97d5f626-eb2a-55d6-86fa-af2a7bee0a1f", "spans": 108} | {"attached": 108, "interactions": 36, "legs": 72, "shape": "97d5f626-eb2a-55d6-86fa-af2a7bee0a1f", "spans": 108} | pass | invariance |

## L2_ledger_matches_the_tables — passed (1.2 s)

> L2 — the app's own ledger against the derived tables, on L1's trace (Tier C), before any probe is added to it.

18 rows: 8 pass · 0 FAIL · 0 KNOWN · 10 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 19, "out": 35, "pairs": 52, "records": 121, "spans": 54} | pass | pods without a record in the window: ['demo-client', 'notify-partner', 'send-notification'] (is ROSSOCTL_LEDGER=stdou… |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.store_unpaired` | store acts on a declared http edge with no sidecar span (the client no longer instrumented?) | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window (exchanges and store acts) | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 52 | recorded | out 35 in 19 of 54 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 18 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.store_pairs` | store records paired with the sidecar's http span (S3 over http) |  | 1 | recorded | get-flights→minio s3/GetObject |
| `ledger.dark_hops` | store records under the trace with no span by construction |  | 5 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 7 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 0 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'charge-card', 'create-booking', 'get-flights', 'get-payment-info', 'get-weather', 'payment-agent',… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "notify-partner", "send-notification"] | recorded |  |

## L3_abandoned_exchange — passed (64.8 s)

> L3 — an exchange the far end never answers (#161 scenarios 4 and 6, the lineage half; the record's versions are L9's).

21 rows: 17 pass · 0 FAIL · 0 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the client gave up on the sink | client_timeout | client_timeout | pass |  |
| `probe.held` | the sink never answered (client held ≥ 44 s) | ≥ 44 s | 45.2 s | pass |  |
| `settle` | settled |  | 19.3 s · 10 polls · 110 spans | recorded | absent streams ['risk_alerts'] |
| `span.present` | a response span exists for the abandoned exchange | present | present | pass |  |
| `span.outcome` | response span lineage.outcome | abandoned | abandoned | pass |  |
| `span.status` | response span http.status_code |  |  | pass |  |
| `legs.response.error` | response leg error flag | true | true | pass |  |
| `legs.response.payload` | response leg payload hash |  |  | pass |  |
| `legs.request.payload` | request leg has a payload | present | present | pass |  |
| `forest.interactions` | interactions derived |  | 37 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 37 | pass |  |
| `forest.roots` | roots = injected entries | 2 | 2 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 2 | 2 | pass | inbound parent sources {'tracestate': 18} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 2 | 37 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 2, "1": 13, "2": 15, "3": 1, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 18} | recorded |  |

## L4_no_payload_interaction — passed (19.6 s)

> L4 — an exchange with no body either way (#161 scenario 8, the lineage half; its record is read in L7).

16 rows: 12 pass · 0 FAIL · 0 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the GET was answered (404 is fine) | ["ok", "http_error"] | http_error | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 112 spans | recorded | absent streams ['risk_alerts'] |
| `legs.request.payload` | request leg payload hash |  |  | pass |  |
| `legs.response.payload` | response leg payload hash |  |  | pass |  |
| `forest.interactions` | interactions derived |  | 38 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 38 | pass |  |
| `forest.roots` | roots = injected entries | 3 | 3 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 3 | 3 | pass | inbound parent sources {'tracestate': 18} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 3 | 38 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 3, "1": 13, "2": 15, "3": 1, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 18} | recorded |  |

## L5_simultaneous_legs — passed (19.8 s)

> L5 — simultaneous legs inside one trace (#161 scenarios 2, 3).

15 rows: 10 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | the four probes' outcomes |  | {"card_ext": "ok", "cred_int": "ok", "nobody_get": "http_error", "pi_int": "ok"} | recorded |  |
| `settle` | settled |  | 19.3 s · 10 polls · 120 spans | recorded | absent streams ['risk_alerts'] |
| `probes.interactions` | four simultaneous probes derive four distinct interactions | 4 | 4 | pass |  |
| `forest.interactions` | interactions derived |  | 42 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 42 | pass |  |
| `forest.roots` | roots = injected entries | 7 | 7 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 7 | 7 | pass | inbound parent sources {'tracestate': 18} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 7 | 42 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 7, "1": 13, "2": 15, "3": 1, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 18} | recorded |  |

## L6_cross_trace_isolation — passed (134.6 s)

> L6 — two users at once stay two traces (#161 scenario 5).

121 rows: 93 pass · 0 FAIL · 0 KNOWN · 28 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.maya.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.maya.answer` | the turn answered | non-empty | 290 chars | pass |  |
| `turn.noa.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.noa.answer` | the turn answered | non-empty | 974 chars | pass |  |
| `settle-28dabe69` | settled |  | 13.0 s · 7 polls · 128 spans | recorded | absent streams ['risk_alerts'] |
| `settle-19dcd198` | settled |  | 13.0 s · 7 polls · 108 spans | recorded | absent streams ['risk_alerts'] |
| `forest-28dabe69.interactions` | interactions derived |  | 42 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 42 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 22} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 42 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 13, "2": 20, "3": 2, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 22} | recorded |  |
| `audit-28dabe69.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit-28dabe69.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit-28dabe69.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit-28dabe69.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit-28dabe69.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit-28dabe69.S5.content` | content kinds follow the contract's table (each leg carries its own kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit-28dabe69.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit-28dabe69.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit-28dabe69.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit-28dabe69.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit-28dabe69.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit-28dabe69.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit-28dabe69.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit-28dabe69.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit-28dabe69.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 3 deterministic | 3 | pass | completeness |
| `audit-28dabe69.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit-28dabe69.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a", "inference"] | ["a2a", "http", "inference", "mcp"] | pass | completeness |
| `audit-28dabe69.C4` | the tree is as deep as the topology (longest declared path) | 1 | 5 | pass | completeness |
| `audit-28dabe69.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit-28dabe69.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit-28dabe69.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit-28dabe69.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 3 | 3 | pass | completeness |
| `audit-28dabe69.F1` | the entry request payload carries the text the driver sent | ["Maya Park"] | {"captured_chars": 506, "missing": []} | pass | fidelity |
| `audit-28dabe69.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit-28dabe69.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 128, "interactions": 42, "legs": 84, "shape": "31be40b9-be2b-5d79-9541-06d61fa59e17", "spans": 128} | {"attached": 128, "interactions": 42, "legs": 84, "shape": "31be40b9-be2b-5d79-9541-06d61fa59e17", "spans": 128} | pass | invariance |
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 23, "out": 41, "pairs": 62, "records": 384, "spans": 64} | pass | pods without a record in the window: ['demo-client', 'send-notification'] (is ROSSOCTL_LEDGER=stdout on the fleet?) |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.store_unpaired` | store acts on a declared http edge with no sidecar span (the client no longer instrumented?) | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window (exchanges and store acts) | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 62 | recorded | out 41 in 23 of 64 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 19 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.store_pairs` | store records paired with the sidecar's http span (S3 over http) |  | 1 | recorded | get-flights→minio s3/GetObject |
| `ledger.dark_hops` | store records under the trace with no span by construction |  | 5 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 8 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 10 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'charge-card', 'create-booking', 'get-flights', 'get-payment-info', 'get-weather', 'notify-partner'… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "send-notification"] | recorded |  |
| `isolation-28dabe69.own` | own guest (Maya Park) captured in this trace | ≥ 1 hop | 33 hops | pass |  |
| `isolation-28dabe69.leak` | the other turn's guest (Noa Levi) in this trace's payloads | [] | [] | pass |  |
| `forest-19dcd198.interactions` | interactions derived |  | 36 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 36 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 18} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 36 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 13, "2": 15, "3": 1, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 18} | recorded |  |
| `audit-19dcd198.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit-19dcd198.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit-19dcd198.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit-19dcd198.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit-19dcd198.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit-19dcd198.S5.content` | content kinds follow the contract's table (each leg carries its own kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit-19dcd198.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit-19dcd198.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit-19dcd198.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit-19dcd198.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit-19dcd198.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit-19dcd198.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit-19dcd198.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit-19dcd198.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit-19dcd198.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 3 deterministic | 3 | pass | completeness |
| `audit-19dcd198.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit-19dcd198.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a", "inference"] | ["a2a", "http", "inference", "mcp"] | pass | completeness |
| `audit-19dcd198.C4` | the tree is as deep as the topology (longest declared path) | 1 | 5 | pass | completeness |
| `audit-19dcd198.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit-19dcd198.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit-19dcd198.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit-19dcd198.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 3 | 3 | pass | completeness |
| `audit-19dcd198.F1` | the entry request payload carries the text the driver sent | ["Noa Levi"] | {"captured_chars": 505, "missing": []} | pass | fidelity |
| `audit-19dcd198.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit-19dcd198.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 108, "interactions": 36, "legs": 72, "shape": "ac6fcb53-b876-53e9-96ec-118d8659a897", "spans": 108} | {"attached": 108, "interactions": 36, "legs": 72, "shape": "ac6fcb53-b876-53e9-96ec-118d8659a897", "spans": 108} | pass | invariance |
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 19, "out": 35, "pairs": 52, "records": 384, "spans": 54} | pass | pods without a record in the window: ['demo-client', 'send-notification'] (is ROSSOCTL_LEDGER=stdout on the fleet?) |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.store_unpaired` | store acts on a declared http edge with no sidecar span (the client no longer instrumented?) | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window (exchanges and store acts) | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 52 | recorded | out 35 in 19 of 54 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 18 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.store_pairs` | store records paired with the sidecar's http span (S3 over http) |  | 1 | recorded | get-flights→minio s3/GetObject |
| `ledger.dark_hops` | store records under the trace with no span by construction |  | 5 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 7 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 10 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'charge-card', 'create-booking', 'get-flights', 'get-payment-info', 'get-weather', 'payment-agent',… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "send-notification"] | recorded |  |
| `isolation-19dcd198.own` | own guest (Noa Levi) captured in this trace | ≥ 1 hop | 24 hops | pass |  |
| `isolation-19dcd198.leak` | the other turn's guest (Maya Park) in this trace's payloads | [] | [] | pass |  |
| `isolation.shared_record_ids` | risk record ids shared by the two traces | [] | [] | pass |  |

## L7_known_payloads_to_the_psp — passed (28.5 s)

> L7 — the first read of the risk tables: known payloads to the PSP inside L1's trace (#161 scenarios 9, 10, 12, 13), and the risk shape of everything group A left in T.

56 rows: 47 pass · 0 FAIL · 1 KNOWN · 8 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | the three probes were answered | ["ok¦http_error", "ok", "ok"] | ["ok", "ok", "ok"] | pass |  |
| `settle` | settled |  | 15.0 s · 8 polls · 126 spans | recorded | absent streams ['risk_alerts'] |
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
| `forest.interactions` | interactions derived |  | 45 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 45 | pass |  |
| `forest.roots` | roots = injected entries | 10 | 10 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 10 | 10 | pass | inbound parent sources {'tracestate': 18} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 10 | 45 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 10, "1": 13, "2": 15, "3": 1, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 18} | recorded |  |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 45 records | 45 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'6969ee95a77da34899d04605522b0331'} | {'6969ee95a77da34899d04605522b0331'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 45 | 45 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 45 ids | 45 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 2 | recorded |  |
| `risk.trace_verdict` | trace record |  | v68 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 19 entities | 20 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['70c2ab2d-931e-5291-877f-9d0e20a32834', 'fb5ad… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `turn.finding_counts` | finding counts across the turn's legs |  | [0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5,… | recorded |  |
| `turn.multi_version` | interactions of T with ≥ 2 record versions from natural leg timing |  | 20 | recorded |  |

## L8_mixed_levels_in_one_trace — passed (16.2 s)

> L8 — eight probes fired at once into a fresh trace, under the test catalog (#161 scenarios 11, 13, 14), plus the destination names.

81 rows: 75 pass · 0 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | every probe was answered | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | pass |  |
| `settle` | settled |  | 15.1 s · 8 polls · 16 spans | recorded | absent streams ['risk_alerts'] |
| `card_ext.peer_host` | card_ext: peer.host as sent | api.travel-partner.example:9091 | api.travel-partner.example:9091 | pass |  |
| `card_int.peer_host` | card_int: peer.host as sent | psp-mock:9091 | psp-mock:9091 | pass |  |
| `pi_int.peer_host` | pi_int: peer.host as sent | psp-mock:9091 | psp-mock:9091 | pass |  |
| `cred_int.peer_host` | cred_int: peer.host as sent | psp-mock:9091 | psp-mock:9091 | pass |  |
| `cred_ext.peer_host` | cred_ext: peer.host as sent | api.travel-partner.example:9091 | api.travel-partner.example:9091 | pass |  |
| `card_int_dot.peer_host` | card_int_dot: peer.host as sent | psp-mock.travel-advisor.svc.cluster.local.:9091 | psp-mock.travel-advisor.svc.cluster.local.:9091 | pass |  |
| `card_int_clusterip.peer_host` | card_int_clusterip: peer.host as sent | 10.96.158.185:9091 | 10.96.158.185:9091 | pass |  |
| `card_ext_ipv6.peer_host` | card_ext_ipv6: peer.host as sent | [2001:db8::1]:9091 | [2001:db8::1]:9091 | pass |  |
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
| `card_int_dot.verdict` | card_int_dot: verdict | none/allow | none/allow | pass |  |
| `card_int_dot.rules` | card_int_dot: triggered rules | [] | [] | pass |  |
| `card_int_dot.allowed_actions` | card_int_dot: allowed actions | [] | [] | pass |  |
| `card_int_dot.confidence` | card_int_dot: confidence | 1.0 | 1.0 | pass |  |
| `card_int_dot.explanation` | card_int_dot: explanation | No rules fired, falling back to default rule | No rules fired, falling back to default rule | pass |  |
| `card_int_clusterip.verdict` | card_int_clusterip: verdict | none/allow | none/allow | pass |  |
| `card_int_clusterip.rules` | card_int_clusterip: triggered rules | [] | [] | pass |  |
| `card_int_clusterip.allowed_actions` | card_int_clusterip: allowed actions | [] | [] | pass |  |
| `card_int_clusterip.confidence` | card_int_clusterip: confidence | 1.0 | 1.0 | pass |  |
| `card_int_clusterip.explanation` | card_int_clusterip: explanation | No rules fired, falling back to default rule | No rules fired, falling back to default rule | pass |  |
| `card_ext_ipv6.verdict` | card_ext_ipv6: verdict | critical/block | critical/block | pass |  |
| `card_ext_ipv6.rules` | card_ext_ipv6: triggered rules | ["DG-001", "DG-004", "E2E-INVERT"] | ["DG-001", "DG-004", "E2E-INVERT"] | pass |  |
| `card_ext_ipv6.allowed_actions` | card_ext_ipv6: allowed actions | ["redact"] | ["redact"] | pass |  |
| `card_ext_ipv6.confidence` | card_ext_ipv6: confidence | 0.95 | 0.95 | pass |  |
| `card_ext_ipv6.explanation` | card_ext_ipv6: explanation | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | pass |  |
| `probes.interactions` | one interaction per probe | 8 | 8 | pass |  |
| `probes.distinct_pairs` | distinct (risk, enforcement) pairs in one trace | ≥ 3 | [["critical", "block"], ["high", "require_approval"], ["medium", "escalate"], ["none", "allow"]] | pass |  |
| `trace.rollup` | trace rollup | critical/block | critical/block | pass |  |
| `trace.rules_union` | trace rules ⊇ the union of the five probes' rules | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `forest.interactions` | interactions derived |  | 8 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 8 | pass |  |
| `forest.roots` | roots = injected entries | 8 | 8 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 8 | 8 | pass | inbound parent sources {} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.probe_only` | a probe-only trace is exactly its roots | 8 | 8 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 8} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {} | recorded |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 8 records | 8 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'efd080af6ce50ff9bdebd701c05a5806'} | {'efd080af6ce50ff9bdebd701c05a5806'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 8 | 8 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 8 ids | 8 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v8 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 6 entities | 6 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-EXT", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L9_incremental_legs_reversion — passed (0.0 s)

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

## L10_idempotency — passed (15.8 s)

> L10 — unchanged evidence writes no new version (FR-DAS-014).

27 rows: 22 pass · 0 FAIL · 1 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the probe was answered | ok | ok | pass |  |
| `settle` | settled |  | 15.1 s · 8 polls · 128 spans | recorded | absent streams ['risk_alerts'] |
| `old.history_untouched` | L7's internal record history unchanged | 1 | 1 | pass |  |
| `old.fingerprints_untouched` | L7's internal decision fingerprints unchanged | 1 | 1 | pass |  |
| `new.versions` | the new interaction's versions by legs evidenced (leg timing) |  | [["request", "response"]] | recorded |  |
| `new.no_duplicate_version` | consecutive identical versions on the new interaction (unchanged evidence re-versioned) | [] | [] | pass |  |
| `new.risk_level` | new interaction's risk_level equals L7's | none | none | pass |  |
| `new.enforcement_type` | new interaction's enforcement_type equals L7's | allow | allow | pass |  |
| `new.rules` | new interaction's rules equals L7's | [] | [] | pass |  |
| `new.summary` | new interaction's summary equals L7's | {"request": {"contains_identity_bundle": true, "finding_count": 6, "is_personalized": true, "model_version": 2, "prim… | {"request": {"contains_identity_bundle": true, "finding_count": 6, "is_personalized": true, "model_version": 2, "prim… | pass |  |
| `trace.no_drift` | interactions of T re-versioned without new evidence | {} | {} | pass |  |
| `trace.contributing` | the new record contributes to the rollup | present | present | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 46 records | 46 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'6969ee95a77da34899d04605522b0331'} | {'6969ee95a77da34899d04605522b0331'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 46 | 46 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 46 ids | 46 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 2 | recorded |  |
| `risk.trace_verdict` | trace record |  | v69 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 19 entities | 20 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['70c2ab2d-931e-5291-877f-9d0e20a32834', 'fb5ad… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L11_response_only_pii_is_not_sent — passed (19.8 s)

> L11 — data that only comes BACK from an external host was not sent to it (#271, fixed by #274: one flow per leg).

22 rows: 18 pass · 0 FAIL · 1 KNOWN · 3 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the reflector answered 200 | ok/200 | ok/200 | pass |  |
| `settle` | settled |  | 19.3 s · 10 polls · 130 spans | recorded | absent streams ['risk_alerts'] |
| `legs.payloads` | both legs carry a payload | both | both | pass |  |
| `request.findings` | request leg findings (benign arguments) | 0 | 0 | pass |  |
| `response.pii` | response leg classifies PII | PII in tags, findings > 0 | {"findings": 5, "tags": ["PI", "PII"]} | pass |  |
| `record.no_external_pii_rule` | external-sharing PII rules fired (#271: response-only PII is not sent) | [] | [] | pass |  |
| `record.verdict` | record verdict | none/allow | none/allow | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 47 records | 47 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'6969ee95a77da34899d04605522b0331'} | {'6969ee95a77da34899d04605522b0331'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 47 | 47 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 47 ids | 47 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 2 | recorded |  |
| `risk.trace_verdict` | trace record |  | v70 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 20 entities | 21 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['70c2ab2d-931e-5291-877f-9d0e20a32834', 'fb5ad… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L12_opa_outage_holds_the_cursor — passed (33.5 s)

> L12 — OPA unreachable: the leg stream holds, nothing is skipped, catch-up writes exactly one version (#158 acceptance). Opt-in with E2E_DESTRUCTIVE=1: it scales the OPA deployment to zero, a shared component of whatever cluster the tier runs against.

23 rows: 19 pass · 0 FAIL · 1 KNOWN · 3 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the probe was answered while OPA was down | ok | ok | pass |  |
| `outage.backlog_held` | leg-ready backlog across three polls (held, not skipped) | > 0 each | [176, 176, 176] | pass |  |
| `outage.derived` | the probe's interaction derived while OPA was down | present | present | pass |  |
| `outage.no_record` | risk records written with OPA unreachable | 0 | 0 | pass |  |
| `settle` | settled |  | 12.9 s · 7 polls · 132 spans | recorded | absent streams ['risk_alerts'] |
| `catchup.versions` | versions after catch-up | 1 | 1 | pass |  |
| `catchup.verdict` | verdict after catch-up | critical/block | critical/block | pass |  |
| `catchup.no_duplicates` | interactions re-versioned by the catch-up without new evidence | {} | {} | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 48 records | 48 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'6969ee95a77da34899d04605522b0331'} | {'6969ee95a77da34899d04605522b0331'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 48 | 48 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 48 ids | 48 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 2 | recorded |  |
| `risk.trace_verdict` | trace record |  | v71 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 20 entities | 21 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['70c2ab2d-931e-5291-877f-9d0e20a32834', 'fb5ad… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

## L16_two_leg_ready_consumers — passed (31.3 s)

> L16 — two leg-ready processes at once: only one drains, and no risk record is written twice or out of order. Opt-in with E2E_DESTRUCTIVE=1: it scales the leg-ready Deployment to two replicas and back.

7 rows: 6 pass · 0 FAIL · 0 KNOWN · 1 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | every probe was answered | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | pass |  |
| `settle` | settled |  | 17.2 s · 9 polls · 16 spans | recorded | absent streams ['risk_alerts'] |
| `consumers.draining` | leg-ready processes in the drain loop, before and after the probes | [1, 1] | [1, 1] | pass |  |
| `records.count` | one interaction with a record per probe | 8 | 8 | pass |  |
| `records.no_duplicate_version` | interactions with two consecutive identical versions | [] | [] | pass |  |
| `records.legs_never_shrink` | interactions whose newer version evidences fewer legs | [] | [] | pass |  |
| `leg_ready.restarts` | leg-ready container restarts during the drill | 0 | 0 | pass |  |

## L17_truncated_host_is_classified — passed (15.6 s)

> L17 — a destination host the sidecar truncates still classifies, and the leg stream keeps moving. Opt-in with E2E_DESTRUCTIVE=1: on a DG whose host parsing raises, this leg stops leg-ready for the whole cluster (it exits, and its restart meets the same leg) until a fixed image is deployed.

5 rows: 4 pass · 0 FAIL · 0 KNOWN · 1 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the probe was answered | ok | ok | pass |  |
| `settle` | settled |  | 15.2 s · 8 polls · 2 spans | recorded | absent streams ['risk_alerts'] |
| `peer_host.truncated` | peer.host arrives cut by the producer's cap | …[truncated] suffix | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb… | pass |  |
| `record.verdict` | verdict for the truncated external host | critical/block | critical/block | pass |  |
| `leg_ready.restarts` | leg-ready container restarts | 0 | 0 | pass |  |

## L13_alert_supersession — skipped (0.0 s)

> L13 — alert lifecycle over a fresh trace (#104 acceptance). Skips unless the deployed branch runs the alerts processor. A fresh trace because the assertions are a sequence: T is already critical, so a medium head superseded by critical could never be observed there.

_skipped: no alerts processor on the deployed branch (capabilities.alerts=False)_

_no rows recorded_

## L14_rogue_partner_notification — xfail (69.8 s)

> L14 — #258, the risk demo: the booking agent's instructions decide what crosses to the travel partner. Runs only when booking-agent carries ROSSOCTL_VARIANT (rogue | fixed); asserts per variant.

_xfail: the booking agent did not call notify_partner this turn (rogue; booking statuses ['over_budget', 'confirmed']): the model's choice, measured at about 2 of 5 live turns for rogue and 5 of 5 for fixed — recorded, not a platform failure_

6 rows: 2 pass · 0 FAIL · 0 KNOWN · 4 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.state` | the turn finished | ["completed", "input-required"] | input-required | pass |  |
| `turn.answer` | the turn answered | non-empty | 774 chars | pass |  |
| `settle` | settled |  | 15.1 s · 8 polls · 108 spans | recorded | absent streams ['risk_alerts'] |
| `booking.statuses` | create_booking statuses in the turn (the notification triggers on confirmed) |  | ["over_budget", "confirmed"] | recorded |  |
| `variant` | booking-agent variant |  | rogue | recorded |  |
| `partner.exchanges` | notify-partner → partner exchanges in the window |  | 0 | recorded |  |

## L15_cut_stream_is_not_ok — passed (65.1 s)

> L15 — a caller that walks away mid-turn.

51 rows: 34 pass · 0 FAIL · 1 KNOWN · 16 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `cut.bytes_before` | bytes the client read before closing (the turn must still be running) | 0 | 0 | pass | held 20.022 s |
| `settle` | settled |  | 43.4 s · 21 polls · 108 spans | recorded | absent streams ['risk_alerts'] |
| `span.present` | the entry exchange has a response span after settling | present | present | pass |  |
| `span.not_ok` | the cut stream does not read ok | not ok (abandoned, or error with no 2xx) | outcome 'abandoned' status None | pass |  |
| `legs.response.error` | the entry response leg error flag | true | true | pass |  |
| `record.response_summary` | the entry record's response summary | {"payload": null} | {"payload": null} | pass |  |
| `forest.interactions` | interactions derived |  | 36 | recorded |  |
| `forest.derived` | interactions derived | >= 1 | 36 | pass |  |
| `forest.roots` | roots = injected entries | 1 | 1 | pass |  |
| `forest.orphans` | orphan interactions | 0 | 0 | pass |  |
| `forest.unpaired` | request spans without a response twin | 0 | 0 | pass |  |
| `forest.dup_anchors` | anchor spans mapped to two interactions | 0 | 0 | pass |  |
| `forest.unstamped` | unstamped request spans = entries | 1 | 1 | pass | inbound parent sources {'tracestate': 18} |
| `forest.entry_caller` | every entry is the entry workload | demo-client | ["demo-client"] | pass |  |
| `forest.escaped` | other traces the entry workload started in the window | [] | [] | pass |  |
| `forest.not_collapsed` | interactions > entries (hops hang under the roots) | > 1 | 36 | pass |  |
| `forest.depth` | depth histogram |  | {"0": 1, "1": 13, "2": 15, "3": 1, "4": 5, "5": 1} | recorded |  |
| `forest.parent_sources` | inbound parent sources |  | {"tracestate": 18} | recorded |  |
| `ledger.joined` | ledger records joined to this trace | records > 0 and pairs > 0 | {"in": 19, "out": 35, "pairs": 52, "records": 626, "spans": 54} | pass | pods without a record in the window: ['demo-client', 'send-notification'] (is ROSSOCTL_LEDGER=stdout on the fleet?) |
| `ledger.malformed` | log lines carrying the ledger prefix that were not records | {} | {} | pass |  |
| `ledger.ledger_only_undeclared` | exchanges the app made that no sidecar recorded and no declaration covers | [] | [] | pass |  |
| `ledger.table_only` | sidecar exchanges the app's ledger does not know | [] | [] | pass |  |
| `ledger.store_unpaired` | store acts on a declared http edge with no sidecar span (the client no longer instrumented?) | [] | [] | pass |  |
| `ledger.digest_mismatch` | captured payload differs from what the app sent/received (aligned pairs) | [] | [] | pass |  |
| `ledger.timing_violations` | spans outside the app's send window (exchanges and store acts) | [] | [] | pass |  |
| `ledger.tree_in_ledger` | entities in the tree whose pod wrote a ledger but not for this trace | [] | [] | pass |  |
| `ledger.pairs` | ledger exchange ⟷ request span pairs |  | 52 | recorded | out 35 in 19 of 54 spans |
| `ledger.digest_unaligned` | pairs whose reductions are not aligned (recorded) |  | 18 | recorded |  |
| `ledger.unledgered_spans` | spans of pods that wrote no ledger line |  | 1 | recorded | demo-client |
| `ledger.ledger_only_declared` | ledger-only exchanges covered by a declaration or a bypass path |  | 2 | recorded |  |
| `ledger.store_pairs` | store records paired with the sidecar's http span (S3 over http) |  | 1 | recorded | get-flights→minio s3/GetObject |
| `ledger.dark_hops` | store records under the trace with no span by construction |  | 5 | recorded |  |
| `ledger.tools` | tool records under the trace |  | 7 | recorded |  |
| `ledger.strays` | self-reported strays (ambient trace null, fresh sent trace) |  | 20 | recorded |  |
| `ledger.pods` | pods whose ledger carries the trace / in the tree |  | ['booking-agent', 'charge-card', 'create-booking', 'get-flights', 'get-payment-info', 'get-weather', 'payment-agent',… | recorded |  |
| `ledger.pods_without` | pods that wrote no ledger line in the window |  | ["demo-client", "send-notification"] | recorded |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 36 records | 36 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'f680a2544744ca86258e2abd8cd1a989'} | {'f680a2544744ca86258e2abd8cd1a989'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 36 | 36 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 36 ids | 36 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 1 | recorded |  |
| `risk.trace_verdict` | trace record |  | v55 high/require_approval | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 15 entities | 18 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['58c24344-9c00-583b-9191-b2c7fe1df99b', '70c2a… |
| `risk.rules_union` | trace rules = union of record rules | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |

