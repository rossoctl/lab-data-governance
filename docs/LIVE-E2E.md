# Live end-to-end tier (`tests/live`, `-m live`)

Real traffic through the cortex lineage sidecar on a pinned travel_advisor app, on the kind
cluster, asserted from the data-governance tables. Nothing is seeded; every row the tier reads
was written by the real chain: sidecar → collector → receiver → `sidecar_interactions` →
classifier → leg-ready + risk observer → OPA → trace trigger → alerts (where deployed).

## Which tier answers which question

| tier | where | what it proves | what it cannot see |
|---|---|---|---|
| unit | `tests/risk/**` | one function's contract | anything across a process boundary |
| Tier-1 in-process | `tests/risk/system/` | the consumers agree with each other over one migrated Postgres, forest shape asserted | leg timing, the NOTIFY/cursor chain as pods, the sidecar, the classifier, app concurrency: it seeds the derived tables itself |
| **live** | `tests/live/` | the whole chain on real traffic: timing of the two legs, versions, fingerprints, cursors as pods, concurrency, the real classifier, destination naming, rules at different levels, alerts | deterministic ordering of legs (it observes what the app did, so it drives the cases it needs with probes) |

The live tier runs only when asked for (`addopts` deselects it) and only on a cluster that
matches the filled pins file exactly.

## Principles

1. **Nothing seeded, nothing truncated.** Traces are addressed by the ids the driver mints.
2. **Shape first, then rows, then versions.** Counts are recorded, never asserted: the LLM's
   plan varies between turns, the forest shape does not.
3. **Settle by draining, never by sleeping.** A trace is done when every deployed stream's
   cursor is at its head and no span arrived for the trace in one quiet window.
4. **Expectation before the run.** Each scenario's docstring is its card.
5. **Pinned inputs, recorded outputs.** Preflight refuses on any mismatch; every run leaves a
   record directory.
6. **Exclusive use.** A live run assumes it is the only client of the cluster: it reads
   `spans`/`interactions` by the trace ids it minted, but the settle (drain) criterion, the
   test catalog in OPA and the risk decisions it asserts are cluster-wide. Another session
   driving traffic, rolling pods or applying a catalog during a run invalidates the record.
7. **Leave the cluster as found.** The test catalog is deployed through the production path
   and restored the same way on teardown, proven both times; the sink is deleted.

## Runbook

Follow the steps in order. Each has the command, the line that means it worked, and what to do
when it did not. Nothing here is run by the tests; steps 1–6 are one-time per cluster and per
commit, step 7 is the run.

**Requires podman and kind.** The scripts under `tests/live/ops/` and the preflight call `podman`
directly (image builds, `podman exec <kind node> crictl images`); a docker host is not supported.

### 0. What you need

Three clones, side by side:

| clone | repository | what to check out |
|---|---|---|
| this repo | `rossoctl/lab-data-governance` | the branch under test |
| the app | `s-and-p-team/agent-examples-snp` | `main` at `642167f` or later: it carries the pinned dependency set (#23), the ledger (#25), `apps/lineage_lab` (#27) and the partner-notification variant (#29) |
| cortex | `rossoctl/cortex` | `main` at `ec6a922` or later: its attach kit keeps the app's own OpenTelemetry core (rossoctl/cortex#1311). On an older kit step 3's bake fails with dependency conflicts |

```sh
git clone https://github.com/s-and-p-team/agent-examples-snp.git     # main
git clone https://github.com/rossoctl/cortex.git                     # main
export E2E_KUBE_CONTEXT=kind-rossoctl
export E2E_AGENT_EXAMPLES_SNP=$PWD/agent-examples-snp
export E2E_CORTEX_DIR=$PWD/cortex
cd lab-data-governance && uv sync        # creates .venv (docs/DEVELOPMENT.md); the commands below run from here
```

The repo commits no lockfile, so the Python test environment is the one input the pins do not
cover. First-time cost on a machine with no cached base layers: steps 1–3 pull and build Go, Python
and the classification model's weights; expect well over the per-step times quoted below.

Checks, all read-only. Every one must pass before step 1:

| check | command | pass |
|---|---|---|
| kubectl points at the cluster | `kubectl config current-context` | `kind-rossoctl` (the commands below and the repo's `deploy/` scripts use the current context; else `kubectl config use-context kind-rossoctl`) |
| the platform's collector | `kubectl -n rossoctl-system get deploy otel-collector` | found, 1/1 |
| the collector feeds data governance | `kubectl -n rossoctl-system get cm otel-collector-config -o yaml \| grep -c otlp/data_governance` | ≥ 1 (else run `deploy/patch-rossoctl-collector.sh`, step 2) |
| a platform-set-up agent namespace | `kubectl -n team1 get cm envoy-config authbridge-runtime-config authbridge-config` | three found (another namespace: `export E2E_PLATFORM_NS=<it>`) |
| the model | `ollama list \| grep 'qwen2.5:7b'` | one line (else `ollama pull qwen2.5:7b`) |
| pods can reach the model | `podman exec rossoctl-control-plane curl -s -m 5 http://host.containers.internal:11434/api/tags \| head -c 40` | starts with `{"models":` |
| the node's process limit | `podman inspect rossoctl-control-plane --format '{{.HostConfig.PidsLimit}}'` | ≥ 8192 (else `podman update --pids-limit 8192 rossoctl-control-plane`) |
| the clones are at or past the pinned commits | `git -C $E2E_AGENT_EXAMPLES_SNP merge-base --is-ancestor 642167f HEAD && git -C $E2E_CORTEX_DIR merge-base --is-ancestor ec6a922 HEAD && echo ok` | `ok` (else step 3's bake fails two steps later) |

Why the process limit: podman gives the kind node container 2048 processes by default, and the
platform plus both fleets run close to that. Over it, forks fail and pods across the whole
cluster crash-loop.

### 1. The sidecar images

```sh
tests/live/ops/build-sidecar.sh
```

Pass: the last line is `>> done: cortex <sha7>`. It builds `authbridge-envoy` and `proxy-init`
from the cortex clone, tagged with the clone's commit, and loads both into kind. cortex registers
its plugins by build tag and refuses an untagged build; the script asks the clone's own
`scripts/profile-tags` for the `envoy` profile in a Go container, so the host needs no Go.

### 2. Data governance, from this checkout

Always run it: it is idempotent (about six minutes with cached layers), and there is no command that
tells whether a stack already on the cluster came from this checkout. Step 7's preflight compares
the DG pods' image ids with the images this step builds, so a stack built elsewhere is refused, not
accepted. These are the repo's own install steps (`docs/RISK-E2E-REPRODUCE.md` §1):

```sh
CONTAINER_TOOL=podman deploy/build-and-load.sh
deploy/patch-rossoctl-collector.sh                      # idempotent: the traces/data_governance pipeline
kubectl apply -f deploy/k8s/
deploy/create-opa-configmap.sh                          # the shipped catalog
kubectl -n data-governance rollout restart deploy --selector app.kubernetes.io/part-of=data-governance   # includes opa
kubectl -n data-governance rollout status deploy --selector app.kubernetes.io/part-of=data-governance --timeout=300s
```

Pass, all three: every pod in `data-governance` is Running;
`kubectl -n data-governance get cm opa-policy -o jsonpath='{.data.rules_source\.json}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'`
prints `1.0.0`; and the API answers from the host,
`curl -s -o /dev/null -w '%{http_code}\n' http://dg.localtest.me:8080/risk/rules` prints `200`
(`E2E_DG_API` overrides the URL). The Deployments' migrate init container brings the database to this
checkout's migration head (the Postgres StatefulSet is not rolled and keeps its data); step 7's preflight
verifies the head. `build-and-load.sh` first refreshes the gitignored `uv.lock`; the working tree stays clean.
`unchanged` from `kubectl apply` means the manifests are the same as the resident stack's, not the
images (the tags are fixed): the restart line is what moves the pods onto the images just built.

If the cluster runs a data-governance stack from another branch, replace it before this step with
the repo's own uninstall, `deploy/dg.sh component uninstall` (it reverts the collector tee and
deletes the namespace with its database): a foreign deployment left in the namespace would bring
the database back to its own migration head on every restart, and preflight would refuse. If that
stack must stay and is at a **later** migration, do not run this step; see `E2E_ACCEPT_ALEMBIC_HEAD`
under "The pins and the migration head".

### 3. The app images

```sh
tests/live/ops/build-app.sh travel_advisor
tests/live/ops/build-app.sh lineage_lab
```

Pass, for each: `No broken requirements found.` and a last line `>> done: <app> at <sha7>`. Each
builds the app image from the clone, runs `pip check`, loads it into kind, and bakes the
propagation shim onto it with the cortex clone's attach kit (the `-otel` image).
Fail with `REFUSING` or a `uv pip check` error during the bake: the cortex clone's kit predates
rossoctl/cortex#1311 (step 0's table: `main` at `ec6a922` or later).

### 4. Deploy and attach

```sh
E2E_VARIANT=rogue tests/live/ops/deploy-app.sh travel_advisor    # namespace travel-advisor
tests/live/ops/deploy-app.sh lineage_lab                         # namespace lineage-lab
```

Pass, for each: a last line `>> done: <app> in <namespace> (app <sha7>, cortex <sha7>). Next:
fill_pins.py <app>`, and above it every listed pod `Running` (13 of travel_advisor's pods and 7
of lineage_lab's show `2/2`: app plus sidecar). About ten minutes for travel_advisor, four for
lineage_lab; the script is idempotent and can be re-run: on a workload that already carries the
sidecar it sets the app, sidecar and proxy-init images and rolls it, so a re-run after a cortex or
app bump reaches every pod.

What it does: applies the app's own manifests (no operator enrollment: the sidecar is attached by
the kit's `sidecar-patch.sh`, capture on); points the four agents at the host's ollama in
plaintext so inference is captured; turns the ledger on (`ROSSOCTL_LEDGER=stdout`); excludes every
non-HTTP egress port the fleet declaration lists (`OUTBOUND_PORTS_EXCLUDE`; the audit's S14
checks it on the pod); rolls one workload at a time, tools first and agents in dependency order,
because an agent resolves its peers once at startup. `E2E_VARIANT=rogue|fixed` sets
`ROSSOCTL_VARIANT` on booking-agent for L14; unset, L14 skips.

### 5. The pins

```sh
tests/live/ops/fill_pins.py travel_advisor
tests/live/ops/fill_pins.py lineage_lab
```

Pass, for each: `wrote tests/live/pins.<app>.local.yaml`, then the values. Fail with `image … is
not in the local store`: the step that builds that image was skipped (1, 2 or 3).

### 6. Exclusive use

Make sure nothing else drives traffic, rolls pods or changes the OPA catalog on this cluster
until the run ends (principle 6).

### 7. Run

```sh
.venv/bin/pytest tests/live -m live -rA                                              # travel_advisor, ~10 min
E2E_APP=lineage_lab E2E_APP_NS=lineage-lab .venv/bin/pytest tests/live -m live -rA   # lineage_lab, ~4 min
```

Add `E2E_DESTRUCTIVE=1` to the first command to run L12, which scales OPA to zero and back;
without it L12 skips. Do not pass `-x`: a scenario that needs L1's trace skips itself when L1 did
not run, and the rest must still run.

Expected today, with `E2E_VARIANT=rogue` and `E2E_DESTRUCTIVE=1`:

| app | pytest summary | notes |
|---|---|---|
| travel_advisor | `13 passed, 2 skipped, 1 xfailed` or `14 passed, 2 skipped` | the two skips are the lab module and L13 (no alerts processor on this branch); L14 passes when the model makes the partner call and xfails when it skips it. KNOWN rows: the content-kind gap on L1/L6, the stale-callee record (#279) on the risk scenarios |
| lineage_lab | `9 passed, 1 skipped, 1 xfailed` | the skip is the travel module; R8 xfails on the classifier finding (last four digits read as PCI). KNOWN rows: the stale-callee record (#279) |

Anything else is a finding. A preflight failure names the pin, the expected value and the
observed one, and sends no traffic.

### 8. After the run

| check | command | pass |
|---|---|---|
| the shipped catalog is back | `kubectl -n data-governance get cm opa-policy -o jsonpath='{.data.rules_source\.json}' \| python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'` | `1.0.0` |
| the sink is gone | `kubectl -n travel-advisor get deploy e2e-sink; kubectl -n lineage-lab get deploy e2e-sink` | `NotFound` twice |
| OPA is up | `kubectl -n data-governance get deploy opa` | `1/1` |
| the reports | `.venv/bin/python tests/live/report.py tests/live/runs/<run dir>` for each of the two runs (without an argument it renders the newest, the lab's) | one table per scenario, the header with pins, capabilities and the catalog proof |

The run directory holds `preflight.json`, `capabilities.json`, `catalog-*.{yaml,json,log}`,
per-scenario directories with their `checks.json`, the run's `checks.csv`, and `verdict.json`.

If a run was killed before its teardown, restore by hand: `deploy/create-opa-configmap.sh &&
kubectl -n data-governance rollout restart deploy/opa`, and
`kubectl -n <app namespace> delete deploy,svc -l app.kubernetes.io/part-of=data-governance-live-e2e`.

## The pins and the migration head

`fill_pins.py` reads every value from the clones and the local image store, never from the
cluster, so preflight's comparison stays a real one. `pins.<app>.local.yaml` is gitignored and
loaded first; the committed `pins.yaml` and `pins.lineage_lab.yaml` are templates that stay
`FILL_ME` and refuse preflight by design. The run record keeps the file that was used.

| key | where it comes from |
|---|---|
| `agent_examples_snp.commit`, `cortex.commit` | `git -C <clone> rev-parse HEAD` |
| `*.image_id`, `shim_image_id`, `sidecar_image_id`, `proxy_init_image_id`, `dg.*_image_id` | `podman inspect --format 'sha256:{{.Id}}' <ref>`: the image ID. A kind-loaded image has no registry digest, so the pod's `imageID` and `crictl images` both report the image ID, and that is what preflight compares |
| `llm.digest` | `ollama show qwen2.5:7b --modelfile` → the `FROM …/blobs/sha256-…` hash |
| `contract.version` | the version line of `docs/sidecar-wire-contract.md` (in the template) |

The migration head is not a pin: preflight reads the head of
`data_governance/db/migrations/versions/` in the checkout under test and requires the deployed
`alembic_version` to equal it. **A new migration therefore needs no change here** — it needs the
DG pods rolled so their migrate init container brings the cluster to the new head; until then
preflight refuses the run and names both revisions.

One opt-in sits beside that check. `E2E_ACCEPT_ALEMBIC_HEAD=<revision>` lets a run proceed on a
cluster whose data-governance stack is at a named **later** revision than this checkout (another
branch's processors over the same derived tables). It takes one exact revision, never a
wildcard; `preflight.json` and the report header record the override next to the checkout's own
head. Unset, the check is strict. A run made under it is evidence about that deployed stack, not
about this checkout's processors.

## What a run does

Tunables: `E2E_SETTLE_TIMEOUT` (600 s), `E2E_QUIET_SECONDS` (12 s, must exceed the 10 s
trace-trigger poll backstop), `E2E_APP_NS`, `E2E_DG_NS`, `E2E_DG_API`, `E2E_KIND_NODE`
(`rossoctl-control-plane`), `E2E_RUN_DIR`; for the ops scripts, `E2E_KIND_CLUSTER` (`rossoctl`)
and `E2E_PLATFORM_NS` (`team1`).

The session: preflight → capabilities (alerts processor? health route?) → deploy
`tests/live/catalog_e2e.json` via `deploy/create-opa-configmap.sh` and prove it with one OPA
decision → apply the sink and the reflector → the scenarios in file order → delete them → restore the shipped
catalog and prove it. Everything lands in `tests/live/runs/<UTC>-<dg-sha7>/`.

**Two apps.** `E2E_APP` selects the declaration under `tests/live/apps/` (default `travel_advisor`;
`lineage_lab` for the controlled app, with `E2E_APP_NS=lineage-lab` and its own
`pins.lineage_lab.yaml`). The travel scenarios skip under the lab and vice versa. The declaration
names the workloads preflight pins, the agents it checks, every allowed edge, the dark acts with
their ports, the turn's deterministic edges, and the **known gaps** (`known_gap`): a check a gap
lists reads KNOWN when it fails, by the gap's id, never silent.

Two of the scenarios depend on what the app deploys: L2 needs every workload to run with
`ROSSOCTL_LEDGER=stdout` (`deploy-app.sh` sets it) and skips otherwise (L6 and
L15 join the ledger too when it is on); L13 needs the alerts processor. Both are probed, never assumed.

## Scenarios

| # | drives | asserts | #161 scenario |
|---|---|---|---|
| L1 | one scripted turn (fresh booking dates) under a minted trace id, nothing else | forest (1 root, 0 orphans, 0 unpaired, parent sources {wire 1, tracestate N}), **the audit** on the four lineage axes with the fleet's declared gaps reported as KNOWN, quiet re-check; the risk tables are not read | 1 |
| L2 | the app's ledger lines for the run window against L1's trace, before any probe | exchange ⟷ span one to one, payload digests equal where the reductions coincide, timing inside the app's window, dark hops listed, strays self-reported, tree pods all wrote the trace | — |
| L3 | card to the never-answering sink, 45 s client timeout | `lineage.outcome=abandoned`, no status, response leg `error=true` with no payload | 4, 6 (lineage half) |
| L4 | GET with no body | both legs without payload | 8 (lineage half) |
| L5 | four probes at once into L1's trace | four interactions; the forest holds (in-trace concurrency) | 2, 3 |
| L6 | two turns at once on fresh traces with different guests | both forests, lineage audits and ledger joins hold; no cross-trace payload leakage; no shared record (cross-trace isolation; red on app commits before agent-examples-snp #17) | 5 |
| L7 | a benign body, then the card payload to the PSP as external name and internal name, in L1's trace | the benign leg zero findings, PUBLIC, none/allow; identical card classification; external critical/block [DG-001, DG-004, E2E-INVERT]; internal none/allow; L4's exchange none/allow; rollup critical/block; then R1 and the risk shape of T | 8, 9, 10, 12, 13 |
| L8 | five probes at once into a fresh trace | three distinct (risk, enforcement) pairs in one trace; the PI-only internal probe's explanation joins two rules (the two combining axes won by different rules); rollup and union of rules | 11, 13, 14 |
| L9 | (L3's exchange) | version 1 `[request]` → version 2 `[request, response]`; ≥ 2 decision fingerprints; verdict from the request leg | 4, 6 (risk half) |
| L10 | the internal card probe again, byte-identical | no re-version anywhere; the new interaction equals the old at v1 | FR-DAS-014 |
| L11 | benign tools/call to an external reflector whose JSON-RPC result is PII | request leg zero findings, response leg PII, no external-sharing rule, none/allow — data that only came back was not sent (#271, fixed by #274) | — |
| L12 | OPA scaled to zero (opt-in) | cursor held, nothing skipped, exactly one version after catch-up | #158 |
| L13 | PI probe, then card external, then again (needs the alerts processor) | medium head → critical head superseding it → duplicate_count 1 | #104 |
| L14 | (#258, `ROSSOCTL_VARIANT` on booking-agent) one scripted turn | rogue: the partner request carries the guest's name, classifies PII and decides critical/block [DG-001]; fixed: `dates` and `note` only, no PII, none/allow; xfails on a turn where the model does not make the call (its choice; coverage) | — |
| L15 | a full turn whose client closes after 20 s without reading a byte | entry response span not `ok` (abandoned), response leg `error=true`, no response payload, the turn still derives under its root; the ledger joins | — |

Order: A. lineage and the plugin (L1–L6, nothing read from the risk tables; every real turn checked by shape and by the ledger), B. classification and risk (L7–L13), C. the app's behaviour (L14–L15, the cut turn last).

Issue #161's scenario 7 (response leg only) is impossible by contract and recorded as N/A.

### The controlled app (`E2E_APP=lineage_lab`)

Each request to lineage_lab carries a plan; the expected forest is computed from the plan and the
test asserts equality with the derived tables, then a gap-free audit, then the egress legs'
classification and decision. Rows follow `tests/live/catalog/scenarios.yaml` (its R1 is L14 on
travel_advisor, so the lab starts at R2).

| # | the plan | asserts beyond forest equality |
|---|---|---|
| R2 | a record with PII read from the store and forwarded to the external partner | partner leg PII, critical/block [DG-001, DG-004] |
| R3 | the same to the partner's internal name | same classification, allowed |
| R4 | annotate, then send | still PII, still blocked |
| R5 | two harmless halves combined into an SSN | the halves alone are not PII, the joined value is |
| R6 | one risky record split into two parts, each sent | each part alone is allowed (today's per-leg judgement) |
| R7 | the SSN re-formatted | still recognised as PII |
| R8 | redacted to the SSN's last four, then sent | predicted allow; xfails today: the classifier reads the four digits as PCI |
| R9 | the record to the model, the model's summary sent | the inference request leg carries PII; what leaves is recorded |
| R10 | two agents in parallel, one tool | fan-in derives as the plan says |
| R11 | the same tool call on a pod-lifetime session and on a per-turn one | the pod-lifetime call lands in its own trace, the per-turn call under the turn |

### Reading the outcomes

- **KNOWN rows** sit inside scenarios that pass. A check listed under a declared gap, or one that
  fails on a filed defect, is recorded as KNOWN with its reason; the scenario's verdict stays green and the
  report header counts KNOWN rows apart from pass. Today: the content-kind gap and booking-agent's
  stale tool-call parent (travel), and the risk record that keeps a stale callee (#279) wherever a
  callee's echo arrives after the record was computed (seen on lab rows, sometimes on L7).
- **L6 was red** until agent-examples-snp #17: every delegation to a peer shared one A2A context
  id, so one guest's data reached the other's prompt. On an app commit before that fix the two
  `isolation-*.leak` rows fail and the report names the finding.
- **L14 is coverage of the model.** Whether the booking agent makes the partner call its
  instructions require is the model's choice on every turn (on the app commit it was measured at
  about 2 of 5 live turns for rogue and 5 of 5 for fixed; `input-required` is every turn's normal
  end state, not a signal). A turn without the call xfails, records the booking statuses and asserts
  nothing about the verdict; the per-variant assertions run only on turns where the call happened.
- **L13** skips without an alerts processor; L12's health-route check is skipped when `/risk/health`
  is not served. Both are probed and recorded in `capabilities.json`.

## The test catalog

`tests/live/catalog_e2e.json` = the shipped catalog byte-identical (DG-001/002/004) plus five
`E2E-*` rules chosen so one trace carries (critical, block), (medium, escalate) and
(high, require_approval), and one decision's two axes are won by different rules:

| rule | fires on | decision |
|---|---|---|
| E2E-PI-INT | PI + INTERNAL, internal sharing | medium / warn |
| E2E-INT-DATA | INTERNAL level, internal sharing | low / escalate / [log] |
| E2E-CRED-INT | CREDENTIALS, internal sharing | high / require_approval / [audit] |
| E2E-CRED-EXT | CREDENTIALS to untrusted external | high / escalate |
| E2E-INVERT | PCI to untrusted external | critical / warn (advisory; block still wins) |

It reaches OPA only through `deploy/create-opa-configmap.sh tests/live/catalog_e2e.json`,
the same compiler and schema check as production. Rule ids are asserted from
`interaction_risk_records.triggered_rule_ids` and the stored decision, never from
`GET /risk/rules`; the run record keeps what that route served (`rules_api.json`). Since #283
the route reads the deployed ConfigMap and follows a change within the kubelet's sync period
(about a minute, measured both ways), so the snapshot, taken right after the apply, may still
show the previous catalog; before #283 the route served the catalog baked into the image.

Side effect while the test catalog is deployed: app hops carrying INTERNAL-level payloads
(dates, cities, amounts in prompts) get low/escalate or medium/warn records. Shape is unchanged.

## False-green drills (the harness must be able to fail)

Each drill breaks one thing the tier relies on and must turn a scenario red; none is run by the
tests. Restore afterwards (step 4 re-attaches; the catalog fixture restores the shipped catalog).

- L1 with one leaf's sidecar emitting nothing must fail the forest check: re-attach one tool
  with the kit directly, `NO_EMIT=1` added to the attach line `deploy-app.sh` uses (in
  `$E2E_CORTEX_DIR/deploy/lineage-attach`: `NAMESPACE=travel-advisor DEPLOY=get-weather
  APP_CONTAINER=mcp APP_IMAGE=<shim image> SIDECAR_IMAGE=<sidecar> PROXY_INIT_IMAGE=<proxy-init>
  CAPTURE_IO=true NO_EMIT=1 ./sidecar-patch.sh`), then run L1.
- L8 against the shipped catalog must fail the distinct-pairs assertion: the catalog fixture
  refuses to start a session on the shipped catalog (its proof expects the `E2E-*` rules), so
  the drill is to point `CATALOG_E2E` in `conftest.py` at the shipped file and drop that proof
  for the run; L8's `probes.distinct_pairs` row reads 1 pair.
- L3 against `psp-mock` instead of the sink must fail: run `-k L1_baseline or L3_abandoned`
  with the test module's `SINK_URL`/`SINK_HOST` set to `PSP_URL`/`EXTERNAL_HOST`; L3's
  `probe.outcome` row expects `client_timeout` and reads `ok`.
