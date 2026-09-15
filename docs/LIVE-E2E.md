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
matches `tests/live/pins.yaml` exactly.

## Principles

1. **Nothing seeded, nothing truncated.** Traces are addressed by the ids the driver mints.
2. **Shape first, then rows, then versions.** Counts are recorded, never asserted: the LLM's
   plan varies between turns, the forest shape does not.
3. **Settle by draining, never by sleeping.** A trace is done when every deployed stream's
   cursor is at its head and no span arrived for the trace in one quiet window.
4. **Expectation before the run.** Each scenario's docstring is its card.
5. **Pinned inputs, recorded outputs.** Preflight refuses on any mismatch; every run leaves a
   record directory.
6. **Leave the cluster as found.** The test catalog is deployed through the production path
   and restored the same way on teardown, proven both times; the sink is deleted.

## One-time: pin and deploy

All commands from the repos' own directories; nothing below is run by the tests.

### 1. The app, pinned

```sh
cd $E2E_AGENT_EXAMPLES_SNP && git rev-parse HEAD        # must equal pins.agent_examples_snp.commit
APP=travel_advisor bash deploy.sh                        # no --with-telemetry: the sidecar observes
podman tag docker.io/agent-examples-snp:latest docker.io/library/agent-examples-snp:e2e-38691b92
podman save docker.io/library/agent-examples-snp:e2e-38691b92 -o /tmp/app.tar
KIND_EXPERIMENTAL_PROVIDER=podman kind load image-archive /tmp/app.tar --name rossoctl
for d in search-destinations create-booking get-payment-info send-notification get-weather \
         get-flights charge-card payment-agent research-agent booking-agent travel-advisor \
         demo-client psp-mock weather-service; do
  kubectl -n travel-advisor set image deploy/$d '*=docker.io/library/agent-examples-snp:e2e-38691b92'
done
for d in payment-agent research-agent booking-agent travel-advisor; do   # plaintext LLM, captured
  kubectl -n travel-advisor set env deploy/$d LLM_URL=http://host.containers.internal:11434/v1 LLM_MODEL=qwen2.5:7b
done
ollama pull qwen2.5:7b && ollama show qwen2.5:7b --modelfile | grep -o 'sha256[-:][0-9a-f]*'
```

### 2. The sidecar and the attach

Kit RECIPE step 1 in the cortex clone (builds `authbridge-envoy` and `proxy-init`, loads them,
exports `SIDECAR_IMAGE` / `PROXY_INIT_IMAGE`), then from `authbridge/demos/lineage-travel/`:

```sh
IMAGE=docker.io/library/agent-examples-snp:e2e-38691b92 \
SHIMMED=docker.io/library/agent-examples-snp-otel:e2e-38691b92 ./attach-fleet.sh
```

`attach-fleet.sh` must pass `$SHIMMED` to the bake as the wrapper tag (one-line cortex change);
before that change the bake produced `-otel:latest` while the patches referenced `$SHIMMED`.

### 3. Data governance

```sh
CONTAINER_TOOL=podman deploy/build-and-load.sh          # assert the image id CHANGED (known false green)
kubectl apply -f deploy/k8s/
deploy/create-opa-configmap.sh                          # shipped catalog
kubectl -n data-governance rollout restart deploy/opa
kubectl -n data-governance rollout restart deploy --selector app.kubernetes.io/part-of=data-governance
```

### 4. Fill `pins.yaml`

| key | command |
|---|---|
| `agent_examples_snp.commit`, `cortex.commit` | `git -C <clone> rev-parse HEAD` |
| `*.image_id`, `shim_image_id`, `sidecar_image_id`, `proxy_init_image_id`, `dg.*_image_id` | `podman inspect --format 'sha256:{{.Id}}' <ref>`: the image ID. A kind-loaded image has no registry digest, so the pod's `imageID` and `crictl images` both report the image ID, and that is what preflight compares |
| `dg.alembic_head` | `kubectl -n data-governance exec data-governance-postgres-0 -- psql -U data_governance -d data_governance -Atc 'select version_num from alembic_version'` |
| `llm.digest` | `ollama show qwen2.5:7b --modelfile` → the `FROM …/blobs/sha256-…` hash |
| `contract.version` | the version line of `docs/sidecar-wire-contract.md` |

Preflight fails on any `FILL_ME` and on any mismatch, naming expected and observed.

## Run

```sh
export E2E_KUBE_CONTEXT=kind-rossoctl
export E2E_AGENT_EXAMPLES_SNP=/path/to/agent-examples-snp
export E2E_CORTEX_DIR=/path/to/cortex
.venv/bin/pytest tests/live -m live -x
```

Optional: `E2E_DESTRUCTIVE=1` runs L9 (scales OPA to zero and back). Tunables:
`E2E_SETTLE_TIMEOUT` (600 s), `E2E_QUIET_SECONDS` (12 s, must exceed the 10 s trace-trigger
poll backstop), `E2E_APP_NS`, `E2E_DG_NS`, `E2E_DG_API`, `E2E_KIND_NODE`, `E2E_RUN_DIR`.

The session: preflight → capabilities (alerts processor? health route?) → deploy
`tests/live/catalog_e2e.json` via `deploy/create-opa-configmap.sh` and prove it with one OPA
decision → apply the sink → L1…L10 → delete the sink → restore the shipped catalog and prove
it. Everything lands in `tests/live/runs/<UTC>-<dg-sha7>/`.

## Scenarios

| # | drives | asserts | #161 scenario |
|---|---|---|---|
| L1 | one scripted turn under a minted trace id | forest (1 root, 0 orphans, 0 unpaired, parent sources {wire 1, tracestate N}), strays characterised, one record per interaction, trace record, a zero-findings leg, quiet re-check | 1, 9 |
| L2 | card payload to the PSP as external name and internal name, in L1's trace | identical classification; external critical/block [DG-001, DG-004, E2E-INVERT]; internal none/allow; rollup critical/block | 10, 12, 13 |
| L3 | five probes at once into a fresh trace | three distinct (risk, enforcement) pairs in one trace; the PI-only internal probe's explanation joins two rules (the two combining axes won by different rules); rollup and union of rules | 11, 13, 14 |
| L4 | card to the never-answering sink, 45 s client timeout | version 1 `[request]` → version 2 `[request, response]`; ≥ 2 decision fingerprints | 4 |
| L5 | (L4's exchange) | `lineage.outcome=abandoned`, response leg `error=true` with no payload, verdict from the request leg | 6 |
| L6 | GET with no body | both legs without payload, none/allow, `{"payload": null}` summaries | 8 |
| L7 | four probes at once; two turns at once with different guests | four interactions; both forests hold; no cross-trace payload leakage; no shared record | 2, 3, 5 |
| L8 | the internal card probe again, byte-identical | no re-version anywhere; the new interaction equals the old at v1 | FR-DAS-014 |
| L9 | OPA scaled to zero (opt-in) | cursor held, nothing skipped, exactly one version after catch-up | #158 |
| L10 | PI probe, then card external, then again (needs the alerts processor) | medium head → critical head superseding it → duplicate_count 1 | #104 |

Issue #161's scenario 7 (response leg only) is impossible by contract and recorded as N/A.

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
`GET /risk/rules`: that route serves the catalog baked into the image, so it disagrees with
the deployed ConfigMap during a run. That disagreement is a product seam, filed as an issue
(the API should read the catalog from the same ConfigMap, and health should compare bundle
versions), not something this tier works around.

Side effect while the test catalog is deployed: app hops carrying INTERNAL-level payloads
(dates, cities, amounts in prompts) get low/escalate or medium/warn records. Shape is unchanged.

## False-green drills (the harness must be able to fail)

- L1 with `NO_EMIT=1` on one leaf's sidecar must fail the forest check.
- L3 against the shipped catalog must fail the distinct-pairs assertion.
- L4 against `psp-mock` instead of the sink must fail the `abandoned` assertion.

## After a run

- `kubectl -n data-governance get cm opa-policy -o jsonpath='{.data.rules_source\.json}' | jq -r .version`
  must print `1.0.0` (the shipped catalog is back).
- `kubectl -n travel-advisor get deploy e2e-sink` must not exist.
- The record directory holds `preflight.json`, `capabilities.json`, `catalog-*.{yaml,json,log}`,
  per-scenario `L<n>/…json`, and `verdict.json`.

## Open items settled on the first run

1. `pi_only()` and `credential()` classifications observed once in `payload_classifications`
   before the predicted tags are frozen.
2. Whether the PSP's plain-JSON reply is ever parser-captured (L2 asserts the response summary
   is `{"payload": null}` first).
3. `abandoned` versus `error` on the sink (L5 asserts `error=true` strictly and `abandoned`).
4. Settled on the first run: under kind + podman a loaded image's pod `imageID` is `sha256:<image ID>` (no registry digest), identical to the `crictl` IMAGE ID column; pins use `podman inspect --format 'sha256:{{.Id}}'`.
5. `E2E_SETTLE_TIMEOUT` calibration from the recorded `settle.json`.
6. The alert floor on the deployed branch (L10's first head is medium at the default `low`).
