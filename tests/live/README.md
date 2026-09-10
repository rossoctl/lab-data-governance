# tests/live — the live end-to-end tier

Level 3 of the risk tests. pytest drives a real turn through the pinned travel-advisor fleet
with the lineage sidecar attached, lets the deployed data-governance processors do their work,
and reads the tables back by the trace id it minted. Nothing is seeded, nothing is truncated:
every row asserted on was written by the real chain (sidecar → collector → receiver →
`sidecar_interactions` → classifier → leg-ready + risk observer → OPA → trace trigger →
alerts where deployed). It runs only under `-m live`, and only on a cluster that matches
`pins.yaml` exactly. Setup, pinning and the run command are in `docs/LIVE-E2E.md`.

## The files

| file | role | what it does |
|---|---|---|
| `conftest.py` | gate | session fixtures: `Env` from `E2E_*` variables, kube + DB access, **preflight** (refuses on any pin mismatch), capabilities probe, the test catalog through the production path, the sink, the run record |
| `pins.yaml` | gate | what the cluster must be: app and cortex commits, every image id, DG migration head, OPA image, LLM model + digest, contract version |
| `driver.py` | drive | makes traffic happen from inside the cluster (`kubectl exec` into `demo-client`, so its sidecar is the entry of every trace): one scripted turn, one probe, both under minted ids; the four payload builders |
| `test_travel_live.py` | drive | scenarios L1–L10, each docstring the expectation card written before the run |
| `settle.py` | read | when the tables are ready to read: every deployed stream's cursor at its head and the trace quiet |
| `shape.py` | read | the assertions: the lineage forest, tolerated strays, the risk invariants, plus the per-interaction readers the scenarios use |
| `report.py` | read | renders one Markdown summary of a run record from the files it left (`python3 tests/live/report.py`) |
| `catalog_e2e.json` | data | the shipped catalog byte-identical plus five `E2E-*` rules, so one trace can carry three verdict levels |
| `k8s/e2e-sink.yaml` | data | a service that accepts a connection and never answers (L4/L5) |
| `__init__.py` | | empty |

## How a run flows

1. Preflight compares the cluster with `pins.yaml` (kube context, contract file byte-identity
   on both sides and at the pinned version, one running 2/2 pod per app workload on the pinned shim image with the
   pinned sidecar, LLM env on the four agents, DG pod images and OPA image, alembic head, every
   pinned image id present in the node's image store) and fails the session on the first
   mismatch, naming expected and observed.
2. The test catalog is deployed with `deploy/create-opa-configmap.sh tests/live/catalog_e2e.json`
   and proven with one OPA decision.
3. Capabilities are probed, never assumed: is an alerts processor deployed (deployment present
   and `trace_risk_records.seq` exists)? does `/risk/health` answer?
4. Per scenario: mint a trace id (and a span id per probe), drive, settle, assert shape, assert
   rows, write the scenario's record files.
5. Teardown even after a failure: the sink is deleted, the shipped catalog is restored through
   the same script and proven again.

Everything lands in `tests/live/runs/<UTC timestamp>-<dg sha7>/`.

## Running it

```sh
export E2E_KUBE_CONTEXT=kind-rossoctl
export E2E_AGENT_EXAMPLES_SNP=/path/to/agent-examples-snp   # for the commit pins
export E2E_CORTEX_DIR=/path/to/cortex
.venv/bin/pytest tests/live -m live -x
```

`E2E_DESTRUCTIVE=1` enables L9 (scales OPA to zero and back). The other tunables
(`E2E_SETTLE_TIMEOUT`, `E2E_QUIET_SECONDS`, namespaces, API URL, run directory) are listed in
`docs/LIVE-E2E.md`.

## What we send

Two kinds of traffic, both from `demo-client`. A **turn** is the app's own scripted user
message to `travel-advisor` under a minted trace id: a whole app run (advisor → research +
booking agents → tools → payment-agent → charge-card → psp-mock, LLM calls on every agent). L1
sends one; L7 sends two at once with different guests, cities and accounts. A **probe** is one
HTTP exchange under a minted trace id and parent span id, so its interaction can be found
afterwards by that parent. Every POST body is wrapped as a JSON-RPC `tools/call`, because the
sidecar captures payloads only through its protocol parsers.

The probe grid. Rows are the payloads `driver.py` builds, with the classification the
classifier gives them. Columns are where they go: the mock PSP by its cluster short name
(`psp-mock:9091`, no dot, structurally internal), the same URL with `Host:
api.travel-partner.example` (dotted, not whitelisted, external), and the sink. Cells: verdict
(risk level / enforcement), the rules that fire under the test catalog, the scenarios that
visit the cell.

| payload | classification | internal | external | sink |
|---|---|---|---|---|
| `benign()` — "healthy, ok" | no findings, PUBLIC | none / allow, no rule — L1 | not driven | — |
| `card_pii()` — PAN, CVV, name, email | PI · PII · PCI, RESTRICTED, identity bundle | none / allow, no real rule (fallback `0000`) — L2, L3, L7, L8 | critical / block, DG-001 · DG-004 · E2E-INVERT, allowed `[redact]`, 0.95, DG-001's explanation alone — L2, L3, L7 (L9, L10) | critical / block from the request leg alone; v1 `[request]` → v2 `[request, response]`, outcome `abandoned` — L4, L5 |
| `pi_only()` — age, city, budget, month | PI, INTERNAL, no bundle | medium / escalate, E2E-INT-DATA · E2E-PI-INT, allowed `[log]`, 0.3, two rules' explanations joined — L3, L7 (L10) | not driven | — |
| `credential()` — a password, no username | PI · CREDENTIALS, RESTRICTED | high / require_approval, E2E-CRED-INT, allowed `[audit]`, 0.7 — L3, L7 | critical / block, DG-004 · E2E-CRED-EXT, no DG-001 (no PII), 0.95 — L3 | — |
| no body — GET a path the PSP does not serve | nothing to classify, both legs NULL | none / allow, summary `{"payload": null}` both sides — L6, L7 | — | — |

Scenarios in parentheses need a capability the deployed branch may lack. Rules are read from
`interaction_risk_records.triggered_rule_ids`; allowed actions, confidence and explanation
from the stored decision in `interaction_policy_decisions`; never from `GET /risk/rules`.

## When the tables are ready to read

There is no terminal state to wait for (a request-only interaction never becomes "complete"),
so `settle.wait_drained` uses the only honest condition: every deployed stream's cursor is at
its head **and** the trace's span set has not changed for one quiet window (12 s, above the
10 s trace-trigger poll backstop, so a NOTIFY-less delivery still lands inside it). The five
streams, in pipeline order: `interactions`, `classification`, `leg_ready`,
`risk_trace_trigger`, `risk_alerts`. A stream gated on a capability the branch lacks is not
waited on and is recorded as absent. On timeout the failure names the per-stream backlog and
the legs held behind an unclassified payload. A quiet re-check afterwards proves nothing moves.

## What shape asserts

**The forest law** (`assert_lineage_forest`), per trace:
- at least one interaction;
- roots == the entries the test injected (the turn, and each probe is its own root);
- zero orphans (a parent id that names no interaction in the trace);
- zero request spans without a response twin (same `lineage.exchange.id`);
- zero anchor spans shared by two interactions;
- the unstamped request spans (parent from the wire or from nothing) are exactly the entries,
  and every one of them is `demo-client`'s; everything else is stamp-parented;
- `demo-client` started no other trace in the window (no escape);
- the forest is not just roots.

**Strays** (`assert_strays_tolerated`): other traces started in the window are tolerated only
as MCP sessions from one of the four agents to one of the seven tools, and none of them may
carry a critical record (a probe payload must not leak into a stray).

**The risk invariants** (`assert_risk_shape`):
- exactly one current risk record per live interaction;
- every record carries this trace id, and the interactions' records do not scatter across
  traces;
- a trace risk record exists with `interaction_count` == the live count;
- both aggregation modes are `severity_max`;
- the trace's contributing ids == the current record set; no ghost record (one whose
  interaction was re-keyed away) contributes;
- the trace's entity set == the union of the interactions' callers and callees;
- the trace's real rules (minus the `0000` fallback sentinel) == the union of the records' rules;
- every payload-bearing leg has a `payload_classifications` row, and no current record was
  computed on a pending leg (`classification_pending` in its summary);
- alerts: when an alerts processor is deployed, the expected number of open heads and the open
  head's level == the trace level; otherwise no alerts rows at all.

Shape is asserted for turns and probes alike. Exact content (rules, decision, allowed actions,
confidence, explanation, versions, fingerprints) is asserted for probes only. Counts (spans,
interactions, depth histogram, findings) are recorded in the run record, never asserted: the
LLM's plan varies between turns, the shape does not.

## The scenarios

| # | drives | asserts | needs |
|---|---|---|---|
| L1 | one turn + a benign probe, fresh trace T | forest, strays, risk shape, the benign leg PUBLIC and none/allow, quiet re-check | |
| L2 | card → external and card → internal, in T | identical classification, opposite verdicts, rollup critical/block | |
| L3 | five probes at once, fresh trace | the five predicted verdicts, three distinct (risk, enforcement) pairs, rollup = union of rules | |
| L4 | card → sink, 45 s client timeout, in T | v1 `[request]` then v2 `[request, response]`, ≥ 2 decision fingerprints | sink |
| L5 | (L4's exchange) | `lineage.outcome=abandoned`, response leg `error=true` with no payload, verdict from the request leg | sink |
| L6 | no-body GET, in T | both legs NULL, none/allow, `{"payload": null}` both sides | |
| L7 | four probes at once in T; then two turns at once on fresh traces | four interactions with their L3 verdicts; both forests hold; no cross-trace payload mention; no shared record id | |
| L8 | the internal card probe again, byte-identical | no re-version anywhere; the new interaction equals the old at v1 | |
| L9 | card → external with OPA scaled to zero, then back | leg-ready holds the cursor, nothing skipped, exactly one version after catch-up | `E2E_DESTRUCTIVE=1` |
| L10 | PI probe, card → external, the same again, fresh trace | one open alert medium → superseded by critical → `duplicate_count` 1 | alerts processor |

Later scenarios address the trace L1 minted, so the file runs in order with `-x`.

## Reading a run record

`python3 tests/live/report.py [runs/<dir>]` renders one Markdown summary of a record (the newest
by default): the verdict table, then per scenario the docstring beside the numbers read. It reads
only the files below and asserts nothing.

| file | what it tells you |
|---|---|
| `preflight.json` | every pin with expected and observed values |
| `capabilities.json` | whether alerts and the health route were present on this branch |
| `catalog-proof.json`, `catalog-restored-proof.json` | the OPA decision that proved the test catalog was live, and the shipped one afterwards |
| `env.json`, `pins.yaml` | the run's inputs, copied |
| `L<n>/forest.json` | the forest numbers: roots, orphans, unpaired, parent sources, depth histogram, escapes |
| `L<n>/risk.json` | what `assert_risk_shape` read: records, trace record, ghosts, unclassified legs, pending records, alerts |
| `L<n>/records.json`, `L<n>/decision_*.json` | the probe interactions' current records and stored decisions |
| `L<n>/settle.json` | how long the trace took to drain and the backlog history |
| `verdict.json` | pass/fail per scenario |
