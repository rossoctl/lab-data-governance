# Reproduce the risk e2e — travel advisor + the two-name probe

The whole demo, from a laptop with podman + a rossoctl kind cluster, in order.
Two repos, two branches:

| repo | branch | provides |
|---|---|---|
| `rossoctl/lab-data-governance` | `risk` | this repo — DG service + risk engine + risk UI (`risk` carries main's sidecar algorithm, PR #177, since PR #233) |
| `s-and-p-team/cortex` | `lineage-dev-R` | a 2026-09-07 snapshot: cortex `main` + the lineage plugin (#761) + the attach kit (#852), as they were before merging, plus the travel demo (`authbridge/demos/lineage-travel/`). Use this branch as is — the kit on today's cortex `main` gained a dependency gate that refuses the travel image (its google-adk pins OpenTelemetry ≤ 1.42.1; the kit installs 1.44) |

What the test shows: one scripted travel-advisor turn under sidecar lineage, then
two identical PII payloads riding the same trace to the same in-cluster mock PSP —
one named by the cluster short name (classifies **internal** → classified,
`none/allow`), one carrying `Host: api.travel-partner.example:9091` (classifies
**external** → classified, `critical/block`, rules DG-001 + DG-004).

## 1 · Data-governance side (this repo, branch `risk`)

```sh
git switch risk

# Build the images and load them into the kind cluster (runs `uv lock` itself;
# on podman v5, if `kind load docker-image` misbehaves, load via
# `podman save … | kind load image-archive`):
./deploy/build-and-load.sh

# Feed the platform collector into the DG receiver (idempotent; skip if the
# cluster's otel-collector config already has the traces/data_governance pipeline):
./deploy/patch-rossoctl-collector.sh

# Apply the manifests (includes leg-ready, the risk trace trigger, and OPA):
kubectl apply -f deploy/k8s/

# Ship the COMPILED policy bundle to OPA and (re)start it:
./deploy/create-opa-configmap.sh
kubectl -n data-governance rollout restart deploy/opa
kubectl -n data-governance rollout restart deploy --selector app.kubernetes.io/part-of=data-governance
```

Checks: DB migration head is `0019_interaction_risk_seq`
(`… psql -c "select version_num from alembic_version"`), and OPA answers —
`POST http://opa:8181/v1/data/data_governance/policy_decision` from any pod
returns a decision (a PII + external_sharing input returns critical/block/DG-001).

> Never run the old `interactions` processor alongside `sidecar_interactions`
> (shared tables); the manifests here deploy only the sidecar algorithm.

## 2 · Sidecar + demo side (cortex clone, branch `lineage-dev-R`)

```sh
git fetch origin && git switch lineage-dev-R
```

Build the sidecar images once (kit RECIPE step 1 — `authbridge/lineage-attach/RECIPE.md`),
export `SIDECAR_IMAGE` / `PROXY_INIT_IMAGE`, then follow
**`authbridge/demos/lineage-travel/README.md`** end to end. In short:

```sh
# in a clone of s-and-p-team/agent-examples-snp (used as-is, zero edits):
APP=travel_advisor bash deploy.sh          # naked deploy — do NOT --with-telemetry
# optional plaintext LLM override (captured inference hops): see demo README step 1

# from authbridge/demos/lineage-travel/ on lineage-dev-R:
./attach-fleet.sh                          # kit shim + sidecar on all 12 Deployments
./ask.sh                                   # the scripted turn; prints the trace id
./risk-probe.sh <trace-id>                 # the two PII probes riding that trace
```

## 3 · What to verify

- `../lineage/show-trace.py <trace-id>` — one root; the two probe exchanges inside
  the trace (openai-agents/langgraph MCP tool calls stray to their own traces —
  known SDK background-task limitation, counted honestly by the script).
- `http://dg.localtest.me:8080/ui/traces/<trace-id>/flow` — the interaction tree,
  with `demo-client → psp-mock:9091` and `demo-client → api.travel-partner.example:9091`.
- `http://dg.localtest.me:8080/ui/risk` — the trace as the top alert, critical/block.
- `http://dg.localtest.me:8080/ui/risk/traces/<trace-id>` — the Alert Execution view
  (#198): the external probe as the red violation edge + the policy-decision stepper.
- In SQL: both probe interactions' `interaction_risk_records` carry an identical
  request classification (PCI/PI/PII, RESTRICTED, identity bundle); the external one
  `critical/block {DG-001,DG-004}`, the internal one `none/allow` with the compiled
  policy's fallback sentinel `"0000"` (that sentinel also shows up in the UI's
  violation counts and Top-rules list — known presentation seam, not a decision bug).

## Known seams

- The travel app's own `run-demo.sh` misbehaves once sidecars are attached (wrong
  exec container + streamed reply buffered by capture) — the demo's `ask.sh` /
  `run-turn.sh` are the drivers to use.
