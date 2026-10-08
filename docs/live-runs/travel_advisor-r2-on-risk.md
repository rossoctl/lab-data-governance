# Live run 2026-10-09T044214Z-f437f67

cluster `kind-rossoctl` · app `travel-advisor` · ns `travel-advisor` · DG `f437f67` · destructive True

**Scenarios**: 3 failed  
**Rows**: 89 — 66 pass · 16 FAIL · 0 KNOWN · 7 recorded  
**Preflight**: 96 pins, mismatches: none  
**Capabilities**: alerts=False health=False ledger=True  
**Catalog**: test catalog proved live by ['E2E-INT-DATA', 'E2E-PI-INT']; shipped catalog restored and proved; `catalog-after.yaml` present.

| scenario | outcome | s | reason / failure |
|---|---|---:|---|
| L8_mixed_levels_in_one_trace | failed | 19.7 | tests/live/checks.py:104: AssertionError |
| L16_two_leg_ready_consumers | failed | 29.2 | AssertionError: 1 expectation(s) failed: |
| L17_truncated_host_is_classified | failed | 242.1 | AssertionError: trace 73ec7bb4c0b84d84cead0a66f0bd2e47 did not settle in 240s: pending={'interactions': 0, 'classific… |

**Known findings reproduced**: none

## L8_mixed_levels_in_one_trace — failed (19.7 s)

> L8 — eight probes fired at once into a fresh trace, under the test catalog (#161 scenarios 11, 13, 14), plus the destination names.

81 rows: 60 pass · 15 FAIL · 0 KNOWN · 6 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | every probe was answered | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | pass |  |
| `settle` | settled |  | 17.2 s · 9 polls · 16 spans | recorded | absent streams ['risk_alerts'] |
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
| `card_int_dot.verdict` | card_int_dot: verdict | none/allow | critical/block | **FAIL** |  |
| `card_int_dot.rules` | card_int_dot: triggered rules | [] | ["DG-001", "DG-004", "E2E-INVERT"] | **FAIL** |  |
| `card_int_dot.allowed_actions` | card_int_dot: allowed actions | [] | ["redact"] | **FAIL** |  |
| `card_int_dot.confidence` | card_int_dot: confidence | 1.0 | 0.95 | **FAIL** |  |
| `card_int_dot.explanation` | card_int_dot: explanation | No rules fired, falling back to default rule | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | **FAIL** |  |
| `card_int_clusterip.verdict` | card_int_clusterip: verdict | none/allow | critical/block | **FAIL** |  |
| `card_int_clusterip.rules` | card_int_clusterip: triggered rules | [] | ["DG-001", "DG-004", "E2E-INVERT"] | **FAIL** |  |
| `card_int_clusterip.allowed_actions` | card_int_clusterip: allowed actions | [] | ["redact"] | **FAIL** |  |
| `card_int_clusterip.confidence` | card_int_clusterip: confidence | 1.0 | 0.95 | **FAIL** |  |
| `card_int_clusterip.explanation` | card_int_clusterip: explanation | No rules fired, falling back to default rule | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | **FAIL** |  |
| `card_ext_ipv6.verdict` | card_ext_ipv6: verdict | critical/block | none/allow | **FAIL** |  |
| `card_ext_ipv6.rules` | card_ext_ipv6: triggered rules | ["DG-001", "DG-004", "E2E-INVERT"] | [] | **FAIL** |  |
| `card_ext_ipv6.allowed_actions` | card_ext_ipv6: allowed actions | ["redact"] | [] | **FAIL** |  |
| `card_ext_ipv6.confidence` | card_ext_ipv6: confidence | 0.95 | 1.0 | **FAIL** |  |
| `card_ext_ipv6.explanation` | card_ext_ipv6: explanation | PII detected in payload sent to UNTRUSTED_EXTERNAL destination. Data exfiltration risk. | No rules fired, falling back to default rule | **FAIL** |  |
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
| `risk.records_in_trace` | every current record names this trace | {'efd4fbcc7c9ab62e52f052bcf847f3ee'} | {'efd4fbcc7c9ab62e52f052bcf847f3ee'} | pass |  |
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

## L16_two_leg_ready_consumers — failed (29.2 s)

> L16 — two leg-ready processes at once: only one drains, and no risk record is written twice or out of order. Opt-in with E2E_DESTRUCTIVE=1: it scales the leg-ready Deployment to two replicas and back.

7 rows: 5 pass · 1 FAIL · 0 KNOWN · 1 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probes.outcomes` | every probe was answered | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"] | pass |  |
| `settle` | settled |  | 14.9 s · 8 polls · 16 spans | recorded | absent streams ['risk_alerts'] |
| `consumers.draining` | leg-ready processes in the drain loop, before and after the probes | [1, 1] | [2, 2] | **FAIL** |  |
| `records.count` | one interaction with a record per probe | 8 | 8 | pass |  |
| `records.no_duplicate_version` | interactions with two consecutive identical versions | [] | [] | pass |  |
| `records.legs_never_shrink` | interactions whose newer version evidences fewer legs | [] | [] | pass |  |
| `leg_ready.restarts` | leg-ready container restarts during the drill | 0 | 0 | pass |  |

## L17_truncated_host_is_classified — failed (242.1 s)

> L17 — a destination host the sidecar truncates still classifies, and the leg stream keeps moving. Opt-in with E2E_DESTRUCTIVE=1: on a DG whose host parsing raises, this leg stops leg-ready for the whole cluster (it exits, and its restart meets the same leg) until a fixed image is deployed.

1 rows: 1 pass · 0 FAIL · 0 KNOWN · 0 recorded

| id | property | expected | actual | status | comment |
|---|---|---|---|---|---|
| `probe.outcome` | the probe was answered | ok | ok | pass |  |

failure outside the table: `AssertionError: trace 73ec7bb4c0b84d84cead0a66f0bd2e47 did not settle in 240s: pending={'interactions': 0, 'classific…`

