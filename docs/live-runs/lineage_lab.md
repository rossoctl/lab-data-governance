# Live run 2026-10-08T061651Z-f0d2808

cluster `kind-rossoctl` · app `lineage-lab` · ns `lineage-lab` · DG `f0d2808` · destructive True

**Scenarios**: 9 passed · 1 xfail  
**Rows**: 576 — 520 pass · 0 FAIL · 3 KNOWN · 53 recorded  
**Preflight**: 61 pins, mismatches: none  
**Capabilities**: alerts=False health=False ledger=True  
**Catalog**: test catalog proved live by ['E2E-INT-DATA', 'E2E-PI-INT']; shipped catalog restored and proved; `catalog-after.yaml` present.

| scenario | outcome | s | reason / failure |
|---|---|---:|---|
| R2_forward_pii_to_external_partner | passed | 18.2 |  |
| R3_forward_pii_to_internal_partner | passed | 20.3 |  |
| R4_annotate_keeps_it_risky | passed | 18.1 |  |
| R5_combine_two_harmless_halves_into_an_ssn | passed | 16.3 |  |
| R6_split_a_risky_record_into_two_harmless_parts | passed | 20.2 |  |
| R7_paraphrase_keeps_the_ssn | passed | 20.1 |  |
| R8_redaction_makes_it_harmless | xfail | 20.2 | the redacted record still decides critical/block ['DG-004', 'E2E-INVERT']: the classifier tags ['PCI', 'PI'] on {publ… |
| R9_llm_summary_leaves | passed | 18.2 |  |
| R10_fan_in_two_agents_one_tool | passed | 20.4 |  |
| R11_pod_lifetime_session_is_its_own_trace | passed | 15.9 |  |

**Known findings reproduced**: risk record keeps the stale peer.host callee (#279)

## R2_forward_pii_to_external_partner — passed (18.2 s)

> R2 — PII read from the store leaves to the external partner unchanged. Expect: forest = plan; the partner request leg carries PII at RESTRICTED; the partner interaction is critical/block with DG-001 (and DG-004).

59 rows: 54 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 2404 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 17.0 s · 9 polls · 26 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 2; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 26, "interactions": 7, "legs": 14, "shape": "4c89c38b-e297-5959-b857-8ea2580c43f1", "spans": 26} | {"attached": 26, "interactions": 7, "legs": 14, "shape": "4c89c38b-e297-5959-b857-8ea2580c43f1", "spans": 26} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 7 records | 7 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'447d2a234f573b622832b408476d7efc'} | {'447d2a234f573b622832b408476d7efc'} | pass |  |
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
| `legs` | interactions by callee with verdicts |  | ["api.partner.example/receive critical/block ['DG-001', 'DG-004']", "lab-a none/allow []", "lab-b none/allow []", "la… | recorded |  |
| `partner.calls` | partner calls | 1 | 1 | pass |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PI", "PII"] | pass |  |
| `partner.level` | partner request leg level | RESTRICTED | RESTRICTED | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |
| `partner.rule` | DG-001 fired | DG-001 | ["DG-001", "DG-004"] | pass |  |

## R3_forward_pii_to_internal_partner — passed (20.3 s)

> R3 — the same flow to the partner by its internal name: same classification, allowed. Precondition: the record classifies above INTERNAL (alice's SSN ⇒ RESTRICTED), so the test catalog's INTERNAL-level rules (E2E-INT-DATA, E2E-PI-INT) do not fire either.

58 rows: 53 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 2404 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.1 s · 10 polls · 26 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 2; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 26, "interactions": 7, "legs": 14, "shape": "74b3e37c-ef4f-512d-8e6a-660562f125f9", "spans": 26} | {"attached": 26, "interactions": 7, "legs": 14, "shape": "74b3e37c-ef4f-512d-8e6a-660562f125f9", "spans": 26} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 7 records | 7 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'4bf01d157be7a698834f4b6659829442'} | {'4bf01d157be7a698834f4b6659829442'} | pass |  |
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
| `legs` | interactions by callee with verdicts |  | ["lab-store/lab_store none/allow []", "lab-store none/allow []", "lab-store none/allow []", "lab-partner/receive none… | recorded |  |
| `partner.calls` | partner calls | 1 | 1 | pass |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PI", "PII"] | pass |  |
| `partner.verdict` | partner verdict (internal) | none/allow | none/allow | pass |  |
| `partner.rules` | real rules fired | [] | [] | pass |  |

## R4_annotate_keeps_it_risky — passed (18.1 s)

> R4 — annotate: PII read from the store leaves with a note added; still PII, still blocked.

59 rows: 54 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 1273 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 17.0 s · 9 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "98e11522-45c5-50ad-921f-4d23a894495e", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "98e11522-45c5-50ad-921f-4d23a894495e", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'e72b7ea82631d5322e5383a2b6a1d1ee'} | {'e72b7ea82631d5322e5383a2b6a1d1ee'} | pass |  |
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
| `legs` | interactions by callee with verdicts |  | ["lab-a none/allow []", "lab-tool-x none/allow []", "lab-store none/allow []", "lab-store none/allow []", "lab-store … | recorded |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PI", "PII"] | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |
| `tool.calls` | lab-tool-x tools/call hops | ≥ 1 | 1 | pass |  |
| `tool.request_pii` | the tool request carries PII | PII | ["PI", "PII"] | pass |  |
| `tool.response_pii` | the tool response carries PII | PII | ["PI", "PII"] | pass |  |

## R5_combine_two_harmless_halves_into_an_ssn — passed (16.3 s)

> R5 — two non-risky payloads become one risky payload. The halves are read separately; the tool joins them; the SSN leaves. Expect: neither half classifies as PII; the tool's response and the partner request do; the partner interaction is critical/block.

58 rows: 52 pass · 0 FAIL · 1 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 442 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 15.1 s · 8 polls · 54 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 54, "interactions": 14, "legs": 28, "shape": "dff7d75e-ad6e-54a1-92ca-f5700ddeaf4d", "spans": 54} | {"attached": 54, "interactions": 14, "legs": 28, "shape": "dff7d75e-ad6e-54a1-92ca-f5700ddeaf4d", "spans": 54} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 14 records | 14 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'076235be2dac3d8312f6e5bcf92e23fa'} | {'076235be2dac3d8312f6e5bcf92e23fa'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 14 | 14 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 14 ids | 14 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v14 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['0141d4d3-5c7b-5f75-be9f-8da3a06b60b4'] missin… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-tool-x high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA', 'E2E-PI-INT']", "lab-store/lab_store none/allow [… | recorded |  |
| `app.joined_ssn` | the app joined the halves into the SSN | 123-45-6789 | 123-45-6789 | pass |  |
| `halves.no_ssn` | neither half's response classifies as SSN | [] | [] | pass |  |
| `partner.tags` | partner request leg tags include PII | PII | ["PII"] | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |

## R6_split_a_risky_record_into_two_harmless_parts — passed (20.2 s)

> R6 — one risky payload becomes two non-risky payloads, each sent externally. Today's per-leg judgement: each part alone is allowed. (#272's column: the SSN's label must reach both parts.)

57 rows: 51 pass · 0 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 1031 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.1 s · 10 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "a16b0b70-0d23-5d29-a030-cfd3f997ad3f", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "a16b0b70-0d23-5d29-a030-cfd3f997ad3f", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'e0ed794f1a1e67bca96b844b27106c2d'} | {'e0ed794f1a1e67bca96b844b27106c2d'} | pass |  |
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
| `legs` | interactions by callee with verdicts |  | ["lab-a none/allow []", "lab-tool-x high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA', 'E2E-PI-INT']", "lab-tool-… | recorded |  |
| `app.parts` | the app split the record into two parts, the SSN only in the suffix part | 2 parts, [no ssn, {ssn_suffix: 6789}] | [{"name": "Alice Moreno", "public_note": "prefers aisle seats", "ssn_prefix": "123-45", "ssn_suffix": "6789"}, {"ssn_… | pass |  |
| `partner.calls` | partner calls | 1 | 1 | pass |  |
| `partner.verdict` | what the classifier made of the two parts together (the label column is empty today) |  | critical/block ['DG-001', 'DG-004'] tags ['PII'] | recorded |  |

## R7_paraphrase_keeps_the_ssn — passed (20.1 s)

> R7 — the SSN re-formatted ('social security no. 123 45 6789'). Prediction: still PII; a miss is a finding about the recogniser, recorded either way.

57 rows: 52 pass · 0 FAIL · 0 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 1219 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.1 s · 10 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "6db93842-c2f1-5b09-9155-8a0d4db2d71d", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "6db93842-c2f1-5b09-9155-8a0d4db2d71d", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'3987f938502e90a898772674c3377ec0'} | {'3987f938502e90a898772674c3377ec0'} | pass |  |
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
| `legs` | interactions by callee with verdicts |  | ["lab-a none/allow []", "lab-tool-x none/allow []", "lab-store none/allow []", "lab-tool-x/lab_tool_x none/allow []",… | recorded |  |
| `partner.classified` | the partner request leg was classified | finding_count present | 7 | pass |  |
| `partner.tags` | the re-formatted SSN is recognised as PII | PII | ["PI", "PII"] | pass |  |
| `partner.verdict` | partner verdict | critical/block | critical/block | pass |  |

## R8_redaction_makes_it_harmless — xfail (20.2 s)

> R8 — the record redacted to the SSN's last four leaves externally: no PII, allowed.

_xfail: the redacted record still decides critical/block ['DG-004', 'E2E-INVERT']: the classifier tags ['PCI', 'PI'] on {public_note, ssn_last4, redacted} — a classifier finding (last four digits read as PCI), recorded_

57 rows: 51 pass · 0 FAIL · 1 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 678 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.2 s · 10 polls · 38 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 38, "interactions": 10, "legs": 20, "shape": "6fdeb1b7-a694-5351-850a-4b9f2b910a88", "spans": 38} | {"attached": 38, "interactions": 10, "legs": 20, "shape": "6fdeb1b7-a694-5351-850a-4b9f2b910a88", "spans": 38} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 10 records | 10 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'dc76ddb281eab9dc5cd63376e70a911c'} | {'dc76ddb281eab9dc5cd63376e70a911c'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 10 | 10 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 10 ids | 10 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v6 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 5 entities | 5 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | ["DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-INVERT", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-a none/allow []", "api.partner.example/receive critical/block ['DG-004', 'E2E-INVERT']", "lab-tool-x/lab_tool_x… | recorded |  |
| `app.redacted` | the app redacted the record | {"public_note": "prefers aisle seats", "redacted": true, "ssn_last4": "6789"} | {"public_note": "prefers aisle seats", "redacted": true, "ssn_last4": "6789"} | pass |  |
| `partner.no_pii` | the redacted record carries no PII | no PII | ["PCI", "PI"] | pass |  |
| `partner.verdict` | the redacted record is allowed | none/allow | critical/block ['DG-004', 'E2E-INVERT'] | KNOWN | classifier finding: the last four digits read as PCI; tags ['PCI', 'PI'] on {public_note, ssn_last4, redacted} |

## R9_llm_summary_leaves — passed (18.2 s)

> R9 — the record goes to the model, the model's summary leaves externally. The llm request leg carries PII; what leaves is measured.

58 rows: 52 pass · 0 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 952 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 15.2 s · 8 polls · 24 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 4 | recorded | depth 1; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 24, "interactions": 7, "legs": 14, "shape": "354458fa-741e-55a7-bce1-18124a5daa0c", "spans": 24} | {"attached": 24, "interactions": 7, "legs": 14, "shape": "354458fa-741e-55a7-bce1-18124a5daa0c", "spans": 24} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 7 records | 7 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'2ab4c71362f22a87d225190067bee9cc'} | {'2ab4c71362f22a87d225190067bee9cc'} | pass |  |
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
| `legs` | interactions by callee with verdicts |  | ["lab-a none/allow []", "lab-store none/allow []", "lab-store/lab_store none/allow []", "api.partner.example/receive … | recorded |  |
| `llm.calls` | inference hops | ≥ 1 | 1 | pass |  |
| `llm.request_pii` | the inference request carries PII | PII | ["PI", "PII"] | pass |  |
| `summary.repeats_ssn` | the model's summary repeats the SSN verbatim |  | true | recorded |  |
| `partner.verdict_follows_classification` | what leaves is judged by what the classifier saw: blocked iff PII | blocked iff PII (PII) | critical/block | pass |  |

## R10_fan_in_two_agents_one_tool — passed (20.4 s)

> R10 — lab-b and lab-c, in parallel, each read a record and forward it to lab-tool-y while both are open; lab-a then sends both externally. Expect: the two lab-tool-y interactions are attributed to their own callers (forest equality says so), two egress interactions, both critical/block.

56 rows: 50 pass · 0 FAIL · 1 KNOWN · 5 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 7403 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 19.1 s · 10 polls · 80 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 8 | recorded | depth 2; absent (pod_lifetime) 0 |
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
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 80, "interactions": 21, "legs": 42, "shape": "180a5081-7d6c-5227-b703-9a99e2b717a3", "spans": 80} | {"attached": 80, "interactions": 21, "legs": 42, "shape": "180a5081-7d6c-5227-b703-9a99e2b717a3", "spans": 80} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 21 records | 21 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'bf1c030ec48a74f8a86a4cb68dc9ed94'} | {'bf1c030ec48a74f8a86a4cb68dc9ed94'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 21 | 21 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 21 ids | 21 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 4 | recorded |  |
| `risk.trace_verdict` | trace record |  | v25 critical/block | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 7 entities | 9 entities | KNOWN | entity set mismatch: stale peer.host callee on a record (#279); extra=['6bfd82ae-c58b-5c42-bd1e-350f7ce0799a', '6ef58… |
| `risk.rules_union` | trace rules = union of record rules | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["DG-001", "DG-004", "E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-a none/allow []", "lab-store high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA', 'E2E-PI-INT']", "api.partne… | recorded |  |
| `partner.calls` | partner calls | 2 | 2 | pass |  |
| `partner.verdicts` | both partner calls blocked | ["critical/block", "critical/block"] | ["critical/block", "critical/block"] | pass |  |

## R11_pod_lifetime_session_is_its_own_trace — passed (15.9 s)

> R11 — the frameworks' failure mode as a switch: the same tool call with session=pod_lifetime must NOT appear in the turn's tree (it rides the startup context into its own trace), while a per_turn call does.

57 rows: 51 pass · 0 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `turn.answer` | lab-a answered | non-empty | 179 chars | pass |  |
| `turn.no_error` | the plan ran without error | no error key | no error key | pass |  |
| `turn.no_step_error` | no step value is an error | [] | [] | pass |  |
| `settle` | settled |  | 15.0 s · 8 polls · 20 spans | recorded |  |
| `forest.expected_calls` | calls the plan predicts (with multiplicity) |  | 2 | recorded | depth 1; absent (pod_lifetime) 1 |
| `forest.missing_calls` | predicted calls missing from the tables | {} | {} | pass |  |
| `forest.extra_calls` | calls in the tables the plan did not predict | {} | {} | pass |  |
| `forest.missing_lifecycle` | predicted lifecycle exchanges missing | [] | [] | pass |  |
| `forest.extra_lifecycle` | lifecycle exchanges not predicted | [] | [] | pass |  |
| `forest.wrong_parent` | calls under the wrong parent | {} | {} | pass |  |
| `forest.entities` | entity set = the plan's | equal | equal | pass |  |
| `forest.equal` | the forest EQUALS the plan's | true | true | pass |  |
| `forest.absent.lab-a>lab-tool-x/lab_tool_x` | a pod_lifetime call stays out of the turn: lab-a→lab-tool-x/lab_tool_x | absent | absent | pass |  |
| `audit.C5.predicted_strays` | strays the plan predicted (pod_lifetime calls) |  | 1 | recorded |  |
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
| `audit.C5` | no activity of the app in the window sits outside the turn, the known traces and the declared gaps | 0 | 1 | pass | completeness |
| `audit.I4` | no critical risk record on a stray trace of the window (nothing of ours leaked there) | 0 | 0 | pass | invariance |
| `audit.S13` | no foreign (non-sidecar) span in the window | 0 | 0 | pass | soundness |
| `audit.C7` | every entity the turn deterministically involves was observed by some sidecar in the window (any trace); the rest rep… | 2 | 2 | pass | completeness |
| `audit.I1` | ids are the contract's uuid5 (trace/anchor; natural_key) | 0 | {"entities": 0, "interactions": 0} | pass | invariance |
| `audit.I3` | a quiet re-read after 0s changes no lineage row | {"attached": 20, "interactions": 5, "legs": 10, "shape": "7fc39700-b829-58c4-aede-70759ddbfaed", "spans": 20} | {"attached": 20, "interactions": 5, "legs": 10, "shape": "7fc39700-b829-58c4-aede-70759ddbfaed", "spans": 20} | pass | invariance |
| `audit.R1` | every current record's evidenced legs are the interaction's legs (one flow per leg) | records = legs present | {"legs_without_record": 0, "mismatch": 0} | pass | risk |
| `audit.no_known_gaps` | the lab declares no gaps: KNOWN checks | [] | [] | pass |  |
| `risk.one_record_per_interaction` | exactly one current risk record per live interaction | 5 records | 5 records | pass |  |
| `risk.records_in_trace` | every current record names this trace | {'11b9cf6b21c90d31405d9c244ba70c0c'} | {'11b9cf6b21c90d31405d9c244ba70c0c'} | pass |  |
| `risk.no_scatter` | the interactions' records live in one trace | 1 | 1 | pass |  |
| `risk.trace_record` | a trace risk record exists | present | present | pass |  |
| `risk.trace_count` | trace record interaction count = live interactions | 5 | 5 | pass |  |
| `risk.trace_modes` | aggregation modes | ["severity_max", "severity_max"] | ["severity_max", "severity_max"] | pass |  |
| `risk.contributing` | contributing ids = the current record set (AC-DAS-009) | 5 ids | 5 ids | pass |  |
| `risk.no_ghost_contributes` | ghost records contributing to the rollup | [] | [] | pass |  |
| `risk.ghosts` | ghost records (of deleted interactions) |  | 0 | recorded |  |
| `risk.trace_verdict` | trace record |  | v5 high/require_approval | recorded |  |
| `risk.entity_set` | trace record entity set = interactions' callers and callees | 3 entities | 3 entities | pass |  |
| `risk.rules_union` | trace rules = union of record rules | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | ["E2E-CRED-INT", "E2E-INT-DATA", "E2E-PI-INT"] | pass |  |
| `risk.unclassified_legs` | payload-bearing legs without a classification | [] | [] | pass |  |
| `risk.pending_records` | current records computed on a pending leg | {} | {} | pass |  |
| `risk.no_alert_rows` | alert rows with no alerts processor deployed | 0 | 0 | pass |  |
| `legs` | interactions by callee with verdicts |  | ["lab-store none/allow []", "lab-store high/require_approval ['E2E-CRED-INT', 'E2E-INT-DATA', 'E2E-PI-INT']", "lab-st… | recorded |  |
| `app.forwarded` | the tool forwarded the record (the call happened, outside the trace) | {"amount": 3200, "city": "Tokyo", "public_note": "office hours 9-5"} | {"amount": 3200, "city": "Tokyo", "public_note": "office hours 9-5"} | pass |  |

