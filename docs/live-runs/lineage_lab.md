# Live run 2026-10-12T155825Z-bc20131

cluster `kind-rossoctl` · app `lineage-lab` · ns `lineage-lab` · DG `bc20131` · destructive True

**Scenarios**: 9 passed · 1 xfail  
**Rows**: 575 — 521 pass · 0 FAIL · 2 KNOWN · 52 recorded  
**Preflight**: 62 pins, mismatches: none  
**Capabilities**: alerts=False health=False ledger=True consumer_lock=False  
**Catalog**: test catalog proved live by ['E2E-INT-DATA', 'E2E-PI-INT']; shipped catalog restored and proved; `catalog-after.yaml` present.

| scenario | outcome | s | reason / failure |
|---|---|---:|---|
| R2_forward_pii_to_external_partner | passed | 20.1 |  |
| R3_forward_pii_to_internal_partner | passed | 19.7 |  |
| R4_annotate_keeps_it_risky | passed | 20.4 |  |
| R5_combine_two_harmless_halves_into_an_ssn | passed | 20.0 |  |
| R6_split_a_risky_record_into_two_harmless_parts | passed | 19.9 |  |
| R7_paraphrase_keeps_the_ssn | passed | 18.1 |  |
| R8_redaction_makes_it_harmless | xfail | 17.9 | the redacted record still decides critical/block ['DG-004', 'E2E-INVERT']: the classifier tags ['PCI', 'PI'] on {publ… |
| R9_llm_summary_leaves | passed | 18.3 |  |
| R10_fan_in_two_agents_one_tool | passed | 20.6 |  |
| R11_pod_lifetime_session_rides_the_turn | passed | 19.9 |  |

**Known findings reproduced**: risk record keeps the stale peer.host callee (#279)

## R2_forward_pii_to_external_partner — passed (20.1 s)

> R2 — PII read from the store leaves to the external partner unchanged. Expect: forest = plan; the partner request leg carries PII at RESTRICTED; the partner interaction is critical/block with DG-001 (and DG-004).

59 rows: 54 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 2404 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 26 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 2; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 2 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 26, "interactions": 7, "legs": 14, "shape": "9f0eb416-de40-5c25-b901-33d83a112d8e", "spans": 26} | {"attached": 26, "interactions": 7, "legs": 14, "shape": "9f0eb416-de40-5c25-b901-33d83a112d8e", "spans": 26} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 7 records | 7 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'7396dd5259976921fbed45e909e8230b'} | {'7396dd5259976921fbed45e909e8230b'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 7 | 7 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 7 ids | 7 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 2 | recorded |  |
| `risk.trace_verdict` | trace record |  | v9 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-store none/allow []", "lab-store none/allow []", "lab-b none/allow []", "lab-store high/require_approval ['E2E-… | recorded |  |
| `partner.calls` | partner calls | 1 | 1 | pass |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PI", "PII"] | pass |  |
| `partner.level` | partner request leg level | RESTRICTED | RESTRICTED | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |
| `partner.rule` | DG-001 fired | DG-001 | ["DG-001", "DG-004"] | pass |  |

## R3_forward_pii_to_internal_partner — passed (19.7 s)

> R3 — the same flow to the partner by its internal name: same classification, allowed. Precondition: the record classifies above INTERNAL (alice's SSN ⇒ RESTRICTED), so the test catalog's INTERNAL-level rules (E2E-INT-DATA, E2E-PI-INT) do not fire either.

58 rows: 53 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 2404 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.1 s · 10 polls · 26 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 2; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 2 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 26, "interactions": 7, "legs": 14, "shape": "e8f7bc2b-3520-5876-b00f-3266ee6aa459", "spans": 26} | {"attached": 26, "interactions": 7, "legs": 14, "shape": "e8f7bc2b-3520-5876-b00f-3266ee6aa459", "spans": 26} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 7 records | 7 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'3baa4776029b876e310be1dad1dabdcb'} | {'3baa4776029b876e310be1dad1dabdcb'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 7 | 7 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 7 ids | 7 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v7 high/require_approval | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-store none/allow []", "lab-a none/allow []", "lab-b none/allow []", "lab-store none/allow []", "lab-partner/rec… | recorded |  |
| `partner.calls` | partner calls | 1 | 1 | pass |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PI", "PII"] | pass |  |
| `partner.verdict` | partner verdict (internal) | none/allow | none/allow | pass |  |
| `partner.rules` | real rules fired | [] | [] | pass |  |

## R4_annotate_keeps_it_risky — passed (20.4 s)

> R4 — annotate: PII read from the store leaves with a note added; still PII, still blocked.

59 rows: 54 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 1273 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.3 s · 10 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 1 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "97549793-4a3e-5247-93b2-863121c46eb1", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "97549793-4a3e-5247-93b2-863121c46eb1", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'bf021bcd94cfa70dab7f4be02c7727b6'} | {'bf021bcd94cfa70dab7f4be02c7727b6'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 10 | 10 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 10 ids | 10 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v10 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-a none/allow []", "lab-tool-x none/allow []", "lab-tool-x high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA'… | recorded |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PI", "PII"] | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |
| `tool.calls` | lab-tool-x tools/call hops | ≥ 1 | 1 | pass |  |
| `tool.request_pii` | the tool request carries PII | PII | ["PI", "PII"] | pass |  |
| `tool.response_pii` | the tool response carries PII | PII | ["PI", "PII"] | pass |  |

## R5_combine_two_harmless_halves_into_an_ssn — passed (20.0 s)

> R5 — two non-risky payloads become one risky payload. The halves are read separately; the tool joins them; the SSN leaves. Expect: neither half classifies as PII; the tool's response and the partner request do; the partner interaction is critical/block.

58 rows: 53 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 442 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 54 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 1 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 54, "interactions": 14, "legs": 28, "shape": "ebcda9a0-fe55-5f78-b199-6bddcd1d14b2", "spans": 54} | {"attached": 54, "interactions": 14, "legs": 28, "shape": "ebcda9a0-fe55-5f78-b199-6bddcd1d14b2", "spans": 54} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 14 records | 14 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'f7678c0e53210f9858a8af3a8e8ac0a8'} | {'f7678c0e53210f9858a8af3a8e8ac0a8'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 14 | 14 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 14 ids | 14 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v14 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-store high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA', 'E2E-PI-INT']", "lab-tool-x high/require_approval … | recorded |  |
| `app.joined_ssn` | the app joined the halves into the SSN | 123-45-6789 | 123-45-6789 | pass |  |
| `halves.no_ssn` | neither half's response classifies as SSN | [] | [] | pass |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PII"] | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |

## R6_split_a_risky_record_into_two_harmless_parts — passed (19.9 s)

> R6 — one risky payload becomes two non-risky payloads, each sent externally. Today's per-leg judgement: each part alone is allowed. (#272's column: the SSN's label must reach both parts.)

57 rows: 51 pass · 0 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 1031 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 1 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "7e157dd4-cac7-5bd1-bda0-25acd0be1ce5", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "7e157dd4-cac7-5bd1-bda0-25acd0be1ce5", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'d96e4f8e0eba17e3c9c232a2790d3269'} | {'d96e4f8e0eba17e3c9c232a2790d3269'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 10 | 10 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 10 ids | 10 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v10 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-tool-x none/allow []", "lab-store/lab_store none/allow []", "api.partner.example/receive critical/block ['DG-00… | recorded |  |
| `app.parts` | the app split the record into two parts, the SSN only in the suffix part | 2 parts, [no ssn, {ssn_suffix: 6789}] | [{"name": "Alice Moreno", "public_note": "prefers aisle seats", "ssn_prefix": "123-45", "ssn_suffix": "6789"}, {"ssn_… | pass |  |
| `partner.calls` | partner calls | 1 | 1 | pass |  |
| `partner.verdict` | what the classifier made of the two parts together (the label column is empty today) |  | critical/block ['DG-001', 'DG-004'] tags ['PII'] | recorded |  |

## R7_paraphrase_keeps_the_ssn — passed (18.1 s)

> R7 — the SSN re-formatted ('social security no. 123 45 6789'). Prediction: still PII; a miss is a finding about the recogniser, recorded either way.

57 rows: 52 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 1219 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 17.0 s · 9 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 1 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "f422b18a-f2a7-54a5-88e5-c459e245b138", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "f422b18a-f2a7-54a5-88e5-c459e245b138", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'1ac576f87ea22bd555a464cd75751434'} | {'1ac576f87ea22bd555a464cd75751434'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 10 | 10 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 10 ids | 10 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v10 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-tool-x high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA', 'E2E-PI-INT']", "api.partner.example/receive crit… | recorded |  |
| `partner.classified` | the partner request leg was classified | finding_count present | 7 | pass |  |
| `partner.tags` | the re-formatted SSN is recognised as PII | PII | ["PI", "PII"] | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |

## R8_redaction_makes_it_harmless — xfail (17.9 s)

> R8 — the record redacted to the SSN's last four leaves externally: no PII, allowed.

_xfail: the redacted record still decides critical/block ['DG-004', 'E2E-INVERT']: the classifier tags ['PCI', 'PI'] on {public_note, ssn_last4, redacted} — a classifier finding (last four digits read as PCI), recorded_

57 rows: 50 pass · 0 FAIL · 2 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 678 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 17.1 s · 9 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 1 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "2c31bc45-5666-5c11-bd5f-6cd72507320b", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "2c31bc45-5666-5c11-bd5f-6cd72507320b", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'7fbba71296dfab192dd436fdbaff6a7c'} | {'7fbba71296dfab192dd436fdbaff6a7c'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 10 | 10 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 10 ids | 10 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v10 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['0141d4d3-5c7b-5f75-be9f-8da3a06b60b4', 'bd91f… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-tool-x/lab_tool_x none/allow []", "lab-store/lab_store none/allow []", "lab-tool-x none/allow []", "lab-store n… | recorded |  |
| `app.redacted` | the app redacted the record | {"public_note": "prefers aisle seats", "redacted": true, "ssn_last4": "6789"} | {"public_note": "prefers aisle seats", "redacted": true, "ssn_last4": "6789"} | pass |  |
| `partner.no_pii` | the redacted record carries no PII | no PII | ["PCI", "PI"] | pass |  |
| `partner.verdict` | the redacted record is allowed | none/allow | critical/block ['DG-004', 'E2E-INVERT'] | KNOWN | classifier finding: the last four digits read as PCI; tags ['PCI', 'PI'] on {public_note, ssn_last4, redacted} |

## R9_llm_summary_leaves — passed (18.3 s)

> R9 — the record goes to the model, the model's summary leaves externally. The llm request leg carries PII; what leaves is measured.

58 rows: 52 pass · 0 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 952 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 15.1 s · 8 polls · 24 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "inference", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 1 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 24, "interactions": 7, "legs": 14, "shape": "8eb4bee8-f974-5b48-b05b-69af0838a9dd", "spans": 24} | {"attached": 24, "interactions": 7, "legs": 14, "shape": "8eb4bee8-f974-5b48-b05b-69af0838a9dd", "spans": 24} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 7 records | 7 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'14fe2fa3c08309e900f31cb4f0e120d5'} | {'14fe2fa3c08309e900f31cb4f0e120d5'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 7 | 7 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 7 ids | 7 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v7 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-store/lab_store none/allow []", "lab-store none/allow []", "lab-a none/allow []", "lab-store high/require_appro… | recorded |  |
| `llm.calls` | inference hops | ≥ 1 | 1 | pass |  |
| `llm.request_pii` | the inference request carries PII | PII | ["PI", "PII"] | pass |  |
| `summary.repeats_ssn` | the model's summary repeats the SSN verbatim |  | true | recorded |  |
| `partner.verdict_follows_classification` | what leaves is judged by what the classifier saw: blocked iff PII | blocked iff PII (PII) | critical/block | pass |  |

## R10_fan_in_two_agents_one_tool — passed (20.6 s)

> R10 — lab-b and lab-c, in parallel, each read a record and forward it to lab-tool-y while both are open; lab-a then sends both externally. Expect: the two lab-tool-y interactions are attributed to their own callers (forest equality says so), two egress interactions, both critical/block.

56 rows: 51 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 7403 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 80 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 8 | recorded | depth 2; on a pod-lifetime session 0 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 2 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 80, "interactions": 21, "legs": 42, "shape": "07eb6281-c7e8-5d8b-b1ca-9e84977b3e50", "spans": 80} | {"attached": 80, "interactions": 21, "legs": 42, "shape": "07eb6281-c7e8-5d8b-b1ca-9e84977b3e50", "spans": 80} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 21 records | 21 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'516f59c2e5a99b3883673daee693799d'} | {'516f59c2e5a99b3883673daee693799d'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 21 | 21 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 21 ids | 21 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v21 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 7 entities | 7 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-b none/allow []", "lab-tool-y/lab_tool_y none/allow []", "lab-store none/allow []", "lab-store/lab_store none/a… | recorded |  |
| `partner.calls` | partner calls | 2 | 2 | pass |  |
| `partner.verdicts` | both partner calls blocked | ["critical/block", "critical/block"] | ["critical/block", "critical/block"] | pass |  |

## R11_pod_lifetime_session_rides_the_turn — passed (19.9 s)

> R11 — the frameworks' failure mode as a switch: the same tool call on a session the agent opened at startup (session=pod_lifetime) derives under the turn exactly like a per-turn call. Before cortex #1333 (the MCP context bridge, lab-dg #287) the call rode the startup context into its own trace; the kit now carries the turn across the session's post task. The switch stays: a kit that loses the bridge turns forest.rides_turn red.

56 rows: 51 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 179 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 24 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 3 | recorded | depth 1; on a pod-lifetime session 1 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `forest.rides_turn.lab-a>lab-tool-x/lab_tool_x` | a pod-lifetime session's call derives under the turn: lab-a→lab-tool-x/lab_tool_x | under the turn | under the turn | pass | lifecycle in the turn: no (ran at startup) |
| `audit.S1` | every span of the trace is attached to exactly one interaction | {"twice": 0, "unattached": 0} | {"twice": 0, "unattached": 0} | pass | soundness |
| `audit.S2` | one anchor per interaction; two spans (bare callee) or four (sidecar'd) | 1 anchor, 2¦4 spans | 0 violations | pass | soundness |
| `audit.S3` | no span without lineage.exchange.id in the trace (producer purity) | 0 | 0 | pass | soundness |
| `audit.S4` | caller/callee entities and leg payloads exist | {"entities": 0, "payloads": 0} | {"entities": 0, "payloads": 0} | pass | soundness |
| `audit.S5` | caller/callee kinds follow the contract's table | as classified | 0 kind violations | pass | soundness |
| `audit.S5.content` | content kinds follow the contract's table (payload identity is content+kind) | as classified | 0 content-kind violations | pass | soundness |
| `audit.S6` | legs mirror spans: request always, response iff response span, payload iff captured, error iff outcome failed | mirror | 0 violations | pass | soundness |
| `audit.S7` | caller = anchor's self, callee = echo's self or peer.host (llm: host/model) | from facts | 0 violations | pass | soundness |
| `audit.S8` | a child's request never precedes its parent's request | 0 | 0 | pass | soundness |
| `audit.S9` | a child's response never follows its parent's response by more than 0.5s | 0 | 0 | pass | soundness |
| `audit.S10` | forest law: roots = unstamped entries, all the entry's; no orphan | {"entry": "demo-client", "orphans": 0, "roots=entries": true} | {"entries": 1, "entry_ids": ["demo-client"], "orphans": 0, "roots": 1} | pass | soundness |
| `audit.S11` | every observed edge is in the declared topology | declared only | 0 undeclared | pass | soundness |
| `audit.S12` | one entity per workload, of its declared kind | one kind each | {"two kinds": {}, "wrong kind": {}} | pass | soundness |
| `audit.S14` | every declared non-HTTP egress port is excluded from interception on its pod | all declared ports excluded | all excluded | pass | soundness |
| `audit.C1` | every entity the turn deterministically involves is in the turn's tree; LLM-chosen coverage reported | 2 deterministic | 2 | pass | completeness |
| `audit.C2` | every deterministic edge of the turn is in the turn's tree; LLM-chosen coverage reported | 1 deterministic | 1 | pass | completeness |
| `audit.C3` | every protocol the turn uses (outside the declared gaps) appears in the tree | ["a2a"] | ["a2a", "mcp"] | pass | completeness |
| `audit.C4` | the tree is as deep as the topology (longest declared path) | 0 | 1 | pass | completeness |
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 0 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 24, "interactions": 6, "legs": 12, "shape": "d9b17108-34c2-5a38-955e-c5af97ad4e5a", "spans": 24} | {"attached": 24, "interactions": 6, "legs": 12, "shape": "d9b17108-34c2-5a38-955e-c5af97ad4e5a", "spans": 24} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 6 records | 6 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'c1f86f3645fc223873a2662d6a0ba818'} | {'c1f86f3645fc223873a2662d6a0ba818'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 6 | 6 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 6 ids | 6 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v6 high/require_approval | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 4 entities | 4 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-store high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA', 'E2E-PI-INT']", "lab-a none/allow []", "lab-tool-x… | recorded |  |
| `app.forwarded` | the tool forwarded the record (the call happened) | {"amount": 3200, "city": "Tokyo", "public_note": "office hours 9-5"} | {"amount": 3200, "city": "Tokyo", "public_note": "office hours 9-5"} | pass |  |

