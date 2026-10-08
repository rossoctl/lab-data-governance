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
| `pins.yaml` | gate | what the cluster must be: app and cortex commits, every image id (app, shim, sidecar, proxy-init, DG), OPA image, LLM model + digest, contract version; the migration head is derived from the checkout, not pinned |
| `fleet.py` + `apps/<app>.yaml` | declare | the one app-specific input: sidecar'd workloads, bare callees, the LLM, every allowed edge, the wire-invisible acts with their ports, the turn's deterministic edges, and the **known gaps** — what the app is known not to deliver today and why |
| `audit.py` | read | every property of the derived tables for one trace on five axes (soundness, completeness, fidelity, invariance, risk); a check a declared gap lists reads KNOWN when it fails, by the gap's id, never silent |
| `ledger.py` | read | Tier C: the app's own ledger (`ROSSOCTL_LEDGER=stdout` in agent-examples-snp) joined to the tables exchange by exchange — digests, timing, dark hops, self-reported strays, turn completeness |
| `lab_expect.py` | read | the expected forest of a lineage_lab plan (the wire contract as a function), the tables read in the same vocabulary, and their diff |
| `test_lab_live.py` | drive | the controlled app's scenarios R2–R11 (`E2E_APP=lineage_lab`), one catalogue row each |
| `catalog/scenarios.yaml` | data | the scenario catalogue: transformation × hop shape × framework × origin × egress, expected graph and per-leg verdicts, the label-propagation column empty (#272) |
| `pins.<app>.yaml` | gate | an app's own template when it has its own image and shim (`pins.lineage_lab.yaml`); `pins.yaml` otherwise. Both stay `FILL_ME`: a run loads the filled, gitignored `pins.<app>.local.yaml` that `ops/fill_pins.py` writes |
| `../live_offline/` | guard | runs in the default suite: the report's finding labels and the expected-forest engine |
| `driver.py` | drive | makes traffic happen from inside the cluster (`kubectl exec` into `demo-client`, so its sidecar is the entry of every trace): one scripted turn, one probe, both under minted ids; the four payload builders |
| `test_travel_live.py` | drive | scenarios L1–L15, each docstring the expectation card written before the run |
| `settle.py` | read | when the tables are ready to read: no exchange of the trace in flight (every request span has its response), the trace quiet, every deployed stream's cursor at its head |
| `checks.py` | read | the recorder: every property examined becomes one row (case, id, property, expected, actual, status, comment) in `<case>/checks.json` and the run's `checks.csv`; KNOWN marks a failure inside a declared gap or a filed defect |
| `shape.py` | read | the assertions: the lineage forest, the risk invariants, plus the per-interaction readers the scenarios use |
| `report.py` | read | renders one Markdown summary of a run record from the files it left (`python3 tests/live/report.py`) |
| `catalog_e2e.json` | data | the shipped catalog byte-identical plus five `E2E-*` rules, so one trace can carry three verdict levels |
| `k8s/e2e-sink.yaml` | data | a service that accepts a connection and never answers (L3, L9), and a reflector whose JSON-RPC result is PII (L11) |
| `ops/` | deploy | what puts the pinned fleet on the cluster, outside the tests: `build-sidecar.sh`, `build-app.sh <app>`, `deploy-app.sh <app>`, `fill_pins.py <app>` (`docs/LIVE-E2E.md`, steps 1–5) |
| `__init__.py` | | empty |

## How a run flows

1. Preflight compares the cluster with the filled pins file (kube context, contract file byte-identity
   on both sides and at the pinned version, one running 2/2 pod per app workload on the pinned shim image with the
   pinned sidecar, LLM env on the four agents, DG pod images and OPA image, the alembic head of this checkout, every
   pinned image id present in the node's image store) and fails the session once, naming
   every mismatch with its expected and observed value.
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

**A live run assumes exclusive use of the cluster.** Drain-based settling, the OPA test
catalog and the risk decisions under assertion are cluster-wide; concurrent traffic, pod rolls
or catalog changes from anyone else invalidate the run record.

```sh
export E2E_KUBE_CONTEXT=kind-rossoctl
export E2E_AGENT_EXAMPLES_SNP=/path/to/agent-examples-snp   # for the commit pins
export E2E_CORTEX_DIR=/path/to/cortex
.venv/bin/pytest tests/live -m live -rA
```

`E2E_DESTRUCTIVE=1` enables L12 (scales OPA to zero and back). `E2E_ACCEPT_ALEMBIC_HEAD=<revision>`
accepts one named later migration head on the cluster (recorded in the run; strict when unset).
The other tunables (`E2E_SETTLE_TIMEOUT`, `E2E_QUIET_SECONDS`, namespaces, API URL, run directory)
are listed in `docs/LIVE-E2E.md`.

## What we send

Two kinds of traffic, both from `demo-client`. A **turn** is the app's own scripted user
message to `travel-advisor` under a minted trace id: a whole app run (advisor → research +
booking agents → tools → payment-agent → charge-card → psp-mock, LLM calls on every agent). L1
sends one; L6 sends two at once with different guests, cities and accounts. A **probe** is one
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
| `benign()` — "healthy, ok" | no findings, PUBLIC | none / allow, no rule — L7 | not driven | — |
| `card_pii()` — PAN, CVV, name, email | PI · PII · PCI, RESTRICTED, identity bundle | none / allow, no real rule (fallback `0000`) — L5, L7, L8, L10 | critical / block, DG-001 · DG-004 · E2E-INVERT, allowed `[redact]`, 0.95, DG-001's explanation alone — L5, L7, L8 (L12, L13) | critical / block from the request leg alone; v1 `[request]` → v2 `[request, response]`, outcome `abandoned` — L3, L9 |
| `pi_only()` — age, city, budget, month | PI, INTERNAL, no bundle | medium / escalate, E2E-INT-DATA · E2E-PI-INT, allowed `[log]`, 0.3, two rules' explanations joined — L5, L8 (L13) | not driven | — |
| `credential()` — a password, no username | PI · CREDENTIALS, RESTRICTED | high / require_approval, E2E-CRED-INT, allowed `[audit]`, 0.7 — L5, L8 | critical / block, DG-004 · E2E-CRED-EXT, no DG-001 (no PII), 0.95 — L8 | — |
| no body — GET a path the PSP does not serve | nothing to classify, both legs NULL | none / allow, summary `{"payload": null}` both sides — L4 (read in L7), L5 | — | — |

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

**The audit** (`audit.run`, L1 and L6 without the risk axis; `audit.risk_only` in L7): every property of the tables for the trace, each check
with expected and observed, never stopping at the first failure. Soundness S1–S14 (attachment,
anchors, producer purity, referential integrity, kinds and content kinds, legs mirror spans,
identities from facts, causal order and containment, the forest law, declared topology only, one
entity per workload, no foreign span, every declared dark port excluded from interception on its
pod); completeness C1–C7 (the deterministic part of the turn is asserted, the LLM-chosen part is
reported as coverage; the window census classes every other trace as known, declared gap, or
escape); fidelity F1–F2 (the entry payloads are the driver's text and the reply); invariance I1–I4
(uuid5 ids, isolation, a quiet re-read, no critical record on a stray); risk R1 (a record's
evidenced legs are the interaction's legs). A check that a gap the fleet declares lists
 (`known_gap` in `apps/travel_advisor.yaml`: the frameworks' pod-lifetime MCP sessions,
booking-agent's stale tool-call parent, the held content-kind fix) reads **KNOWN** — the run stays green on
the honest number and every record and report names the gap.

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
| **A. lineage and the plugin** — nothing read from the risk tables | | | |
| L1 | one turn (fresh booking dates), fresh trace T, nothing else | forest (1 root), **the audit** on the four lineage axes (declared gaps KNOWN), quiet re-check | |
| L2 | (T) the workloads' ledger lines for the run window | every exchange the app made has its sidecar span and vice versa, captured payloads equal the digests where the reductions coincide, spans inside the app's send windows, dark hops listed, strays self-reported, every pod in the tree wrote the trace | app pods with `ROSSOCTL_LEDGER=stdout` |
| L3 | card → sink, 45 s client timeout, in T | `lineage.outcome=abandoned`, no status, response leg `error=true` with no payload, request leg with payload | sink |
| L4 | no-body GET, in T | both legs NULL | |
| L5 | four probes at once in T | four interactions, forest holds (in-trace concurrency) | |
| L6 | two turns at once on two fresh traces, different guests | both forests, lineage audits and ledger joins hold; no cross-trace payload mention (I2); no shared record id (cross-trace isolation; red on app commits before agent-examples-snp #17, the shared A2A context) | |
| **B. classification and risk** — known bytes under the test catalog | | | |
| L7 | benign, card → external and card → internal, in T | the benign leg PUBLIC and none/allow; identical card classification, opposite verdicts; L4's exchange none/allow with `{"payload": null}` both sides; rollup critical/block; then R1 and the risk shape of T (an entity-set mismatch is the open defect #279: a KNOWN row) | |
| L8 | five probes at once, fresh trace | the five predicted verdicts, three distinct (risk, enforcement) pairs, rollup = union of rules | |
| L9 | (L3's exchange) | v1 `[request]` then v2 `[request, response]`, ≥ 2 decision fingerprints, verdict from the request leg | sink |
| L10 | the internal card probe again, byte-identical | no re-version anywhere; the new interaction equals the old at v1 | |
| L11 | benign tools/call to the external reflector whose result is PII, in T | request leg zero findings, response leg PII; no external-sharing rule fires — data that only came back was not sent (#271/#274); none/allow | sink |
| L12 | card → external with OPA scaled to zero, then back | leg-ready holds the cursor, nothing skipped, exactly one version after catch-up | `E2E_DESTRUCTIVE=1` |
| L13 | PI probe, card → external, the same again, fresh trace | one open alert medium → superseded by critical → `duplicate_count` 1 | alerts processor |
| **C. the app's behaviour** | | | |
| L14 | (#258, `ROSSOCTL_VARIANT` on booking-agent) one scripted turn | rogue: the partner request carries the guest's name, classifies PII, critical/block [DG-001]; fixed: `dates` and `note` only, no PII, none/allow; xfails on a turn where the model does not make the call | variant |
| L15 | a full turn whose client closes after 20 s without reading | the entry response span is not `ok` (abandoned), response leg `error=true`, no response payload, the rest of the turn derives under the root; the ledger joins | runs last |

Later scenarios address the trace L1 minted, so the file runs in order; a scenario that needs
L1's trace skips itself when L1 did not run. Do not pass `-x`: one red scenario must not stop the
rest, and a red scenario is a finding to read, not a reason to stop.

**Assertion or coverage.** Every row above asserts shape and the per-leg verdicts of the hops that
happened. Three rows end in `xfail` on a specific, recorded outcome rather than red, because the
outcome is not the platform's: L14 when the model does not make the partner call it was instructed to
make (its choice on every turn; measured at about 2 of 5 live turns for rogue, 5 of 5 for fixed),
R7 if the re-formatted SSN is not recognised (it is, today), R8 when the classifier reads the redacted
record's last four digits as PCI (it does, today — a classifier finding). Those rows are coverage of
the app and the classifier; everything else is an assertion on lineage and risk.

**Host prerequisites.** `kubectl` with the kind context, `podman` (preflight reads the kind node's image
store with `podman exec <node> crictl images`), the `ollama` CLI (the LLM digest), and the two
sibling clones named by `E2E_AGENT_EXAMPLES_SNP` / `E2E_CORTEX_DIR` at the pinned commits.

## Reading a run record

`python3 tests/live/report.py [runs/<dir>]` renders one Markdown summary of a record (the newest
by default; `--csv` prints the run's rows, `diff <A> <B>` what changed between two records): the verdict table, then per scenario the docstring beside the numbers read. It reads
only the files below and asserts nothing.

| file | what it tells you |
|---|---|
| `preflight.json` | every pin with expected and observed values |
| `capabilities.json` | whether alerts and the health route were present on this branch |
| `catalog-proof.json`, `catalog-restored-proof.json` | the OPA decision that proved the test catalog was live, and the shipped one afterwards |
| `env.json`, `pins.<app>.local.yaml` | the run's inputs, copied |
| `L<n>/forest.json` | the forest numbers: roots, orphans, unpaired, parent sources, depth histogram, escapes |
| `L<n>/risk.json` | what `assert_risk_shape` read: records, trace record, ghosts, unclassified legs, pending records, alerts |
| `L<n>/records.json`, `L<n>/decision_*.json` | the probe interactions' current records and stored decisions |
| `L<n>/checks.json`, `checks.json`, `checks.csv` | the check rows, per scenario and for the run |
| `rules_api.json`, `catalog-before.yaml`, `catalog-after.yaml`, `catalog-*.log` | what `GET /risk/rules` served, and the OPA ConfigMap before the test catalog and after the restore |
| `L<n>/settle.json` | how long the trace took to drain and the backlog history |
| `verdict.json` | pass/fail per scenario |
