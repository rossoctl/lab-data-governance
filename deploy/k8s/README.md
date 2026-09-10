# Data-governance Kubernetes manifests (v1)

These manifests stand up the v1 deployment topology pinned by PROJECT.md
§1 / §7:

| File                          | Resources                                                   |
| ----------------------------- | ----------------------------------------------------------- |
| `00-namespace.yaml`           | `Namespace data-governance`                                 |
| `10-postgres-secret.yaml`     | `Secret data-governance-postgres`                           |
| `20-postgres.yaml`            | `Service` + `StatefulSet` for Postgres                      |
| `30-receiver.yaml`            | `Service` + `Deployment` for the OTLP receiver              |
| `40-ui.yaml`                  | `Service` + `Deployment` for the UI backend                 |
| `50-networkpolicy.yaml`       | `NetworkPolicy` for receiver, UI, and Postgres ingress      |
| `60-ui-httproute.yaml`        | `HTTPRoute` + `ReferenceGrant` exposing the UI on the rossoctl shared Gateway |
| `70-interactions.yaml`        | `Deployment` for the P-interactions processor (no Service)  |
| `80-classification.yaml`      | `Deployment` for the P-classification processor (no Service) |
| `90-data-lineage.yaml`        | `Deployment` for the P-data-lineage processor (no Service)  |

## Primary entry point: `deploy/dg.sh`

The recommended way to drive this deployment is the **`deploy/dg.sh`**
cluster-management script. It folds the whole component lifecycle into three
verbs and adds namespace lineage activation:

```sh
./deploy/dg.sh component install     # build + kind-load + apply + collector tee + rollout
./deploy/dg.sh component status      # deployments present/ready + is the tee wired
./deploy/dg.sh component uninstall   # the inverse; --keep-data preserves the Postgres PVC

./deploy/dg.sh namespaces list                              # user namespaces
./deploy/dg.sh --cortex-local-path <cortex> namespace <ns> instrument [<entity>]
./deploy/dg.sh namespace <ns> status [<entity>]             # read-only: did activation take?
```

`dg.sh component install` **wraps the underlying steps documented in the rest
of this file** — `build-and-load.sh` → `kubectl apply -f deploy/k8s/` →
`patch-rossoctl-collector.sh` (the tee) → `rollout restart` + `rollout status`.
The raw steps remain here so you can drive them individually; `dg.sh` is the
primary path, not a replacement that hides them. `component install` is
idempotent (pass `--no-build` when the images are already loaded), and it
performs the load-bearing `rollout restart` for you.

The design and its boundary decisions live in
[`../../docs/cli.md`](../../docs/cli.md),
[ADR-0031](../../docs/adr/0031-non-reversible-namespace-lineage-activation.md)
(namespace activation is **non-reversible** and **mode-preserving** — no
`reset`, no sidecar-mode switch in v1), and
[ADR-0032](../../docs/adr/0032-dg-sh-builds-on-cortex-lineage-attach-kit.md)
(`instrument` drives the cortex lineage-attach kit rather than a vendored
generator).

### Namespace lineage activation (`dg.sh namespace instrument`)

`dg.sh namespace <ns> instrument [<entity>]` switches on lineage telemetry for
the agents/tools in a namespace (all of them, or the single named `<entity>`),
so their traffic produces the facts-only spans the interactions processor
consumes. It is **additive-only and never switches a namespace's sidecar mode**,
and it is **non-reversible in v1** (to undo, delete/redeploy the workloads); its
read-only partner `dg.sh namespace <ns> status [<entity>]` reports, per entity,
sidecar presence, sidecar type, and whether the `lineage-telemetry` plugin is
wired. See ADR-0031 for why.

`instrument` — and **only** `instrument` — needs a cortex checkout on disk,
passed with `--cortex-local-path <dir>` (or the `CORTEX_LOCAL_PATH` env var).
`dg.sh` locates the lineage-attach kit under
`<dir>/authbridge/lineage-attach/`; a missing path or an absent kit is a
**refuse-and-mutate-nothing preflight** (it fails loud and changes nothing). The
component verbs (`install`/`uninstall`/`status`) and `namespace status` never
touch cortex.

The cortex kit is **envoy-sidecar-only**: it owns the no-sidecar (inject envoy)
and envoy-sidecar-in-place rows. The **proxy-sidecar-in-place** row is
**`dg.sh`'s own** edit — the kit has no proxy path (ADR-0032). Both modes work
because cortex PR 760 makes the proxy-sidecar outbound listener propagate the
`dg-parent` stamp too. On a proxy-sidecar entity whose namespace has
`egressEnforcement: none`, `instrument` **warns** loudly (outbound calls are
then not captured); envoy-sidecar captures transparently, so no warning there.

This `dg.sh namespace instrument` proxy-sidecar row is the **productized form of
the ad-hoc proxy-sidecar attach recipe** the root `CLAUDE.md` § 3a and
`LINEAGE-PROXY-SIDECAR-RECIPE.md` document (the hand-run `instrument-one.sh`
loop). Where the demo stays on the proxy shape, `dg.sh`'s proxy row
**supersedes** that manual recipe for the productized flow; the envoy rows are
delegated wholesale to the cortex kit.

### End-to-end operator scenario

The full flow, in one place — from a bare rossoctl cluster to observing the
trace in the UI:

1. Install rossoctl on the cluster.
2. `./deploy/dg.sh component install`.
3. Install the agents/tools (via the rossoctl UI, or an `agent-examples`-style
   deploy — **install only**, do not run them yet).
4. `./deploy/dg.sh --cortex-local-path <cortex-checkout> namespace <ns> instrument`
   on the namespace holding those workloads — the path locates the cortex
   lineage-attach kit. (Scope to one entity by appending its
   `app.kubernetes.io/name`.)
5. Run the agents.
6. Observe the resulting traces in the data-governance UI at
   **<http://dg.localtest.me:8080/>** (see "UI access via the rossoctl shared
   Gateway" below). Confirm activation took with
   `./deploy/dg.sh namespace <ns> status`.

## Topology summary

- **Receiver Deployment.** 2 replicas. Init container runs
  `python -m data_governance.db.migrate` to head; main container runs
  `python -m data_governance.processors.otlp_receiver`. PROJECT.md §3 +
  ADR-0002.
- **Receiver Service.** ClusterIP. Ports: 4317 (OTLP gRPC), 4318
  (OTLP HTTP/protobuf and `/healthz`), 9090 (Prometheus `/metrics`,
  PROJECT.md §5.1; issue #36 wired `MetricsServer` into the receiver
  entry point). The v1 NetworkPolicy does NOT open 9090 — v1 has no
  monitoring peer; adding a scraper ingress rule is a v2 concern.
- **Postgres StatefulSet.** Single replica, 10Gi PVC. Same namespace as
  receiver. Production-grade Postgres operations (backup, replication,
  sizing) are out of scope for v1 (PROJECT.md §1, §5).
- **UI backend Deployment.** Single replica running `python -m
  data_governance.api` on port 8080. Serves both the `/api/` REST
  resource tree and the `/ui/` static UI shell.
- **P-interactions processor Deployment.** **Single replica** running
  `python -m data_governance.processors.interactions` on the receiver
  image (`data-governance/receiver:latest`, `command:` override — no new
  image). Init container runs `python -m data_governance.db.migrate` to
  head like the receiver (ADR-0002). It is a DB consumer with **no
  Service and no container ports**: it polls the `spans` table by `seq`
  and writes the derived interactions tables (migration 0004), advancing
  one shared `processor_state` cursor. Single replica because the driver
  has no inter-pod lock — two pods would share the one cursor and
  double-process; the rollout uses `maxSurge:0`/`maxUnavailable:1` so the
  old pod is gone before the new one starts. It serves a Prometheus
  `/metrics` surface on 9091 in-pod, but — like the receiver's 9090 — v1
  has no monitoring peer, so the port is left undeclared (a v2 concern).
- **P-classification processor Deployment.** **Single replica** running
  `python -m data_governance.processors.classification`. Unlike the other
  three processors it runs its **own** image,
  `data-governance/classification:latest` (issue #79 / ADR-0022) — the "fat"
  image built from `Containerfile.classification` with torch + transformers
  (behind the `classification` extra) and the ~500 MB fine-tuned NER model
  weights + config baked in (git-LFS materialized at build; image tag ↔
  `model_version`, ADR-0023). Both the migrate init container and the processor
  container run this one image, so the pod is self-contained. The model loads
  **in-process** at startup and classifies each **Payload**'s **Classifiable
  text** into **Findings** inline in the drain loop — no separate inference
  service (ADR-0023). Like the interactions processor it is a DB consumer with
  **no Service and no container ports**, single replica against one shared
  `processor_state` cursor (the `classification` row), `maxSurge:0` rollout.
  Higher memory limits (3Gi) than the other processors to hold torch + the
  resident model.
- **P-data-lineage processor Deployment.** **Single replica** running
  `python -m data_governance.processors.data_lineage` on the **shared** receiver
  image (`data-governance/receiver:latest`, `command:` override — no new image;
  ADR-0022's dedicated-image reasoning is specific to classification's torch +
  baked weights and does not apply here). Init container runs `python -m
  data_governance.db.migrate` to head (ADR-0002). A DB consumer with **no
  Service and no container ports** — and, unlike both siblings, no Prometheus
  `/metrics` surface at all (issue #117 ships no counters). It drains
  `interaction_legs` by `seq`, woken by migration 0010's `dg_legs_inserted`
  NOTIFY channel, and writes `lineage_metadata` (migration 0011/0013) plus
  `lineage_trace_status` (migration 0012), advancing its own `processor_state`
  cursor (the `data_lineage` row). Single replica for the same no-inter-pod-lock
  reason, `maxSurge:0` rollout; a brief overlap is safe because each derivation
  is a deterministic function of committed state written entirely inside the
  loop's one transaction (ADR-0007), so concurrent derivations of a trace
  converge — wasted work, not corruption. `SEMANTIC_MATCHER` is pinned to
  `simple`, the trivial always-match matcher (ADR-0028): lineage is complete but
  full of maybes, and setting it explicitly makes that visible. Sized like the
  interactions processor (no model, no inference).
- **NetworkPolicy.** Three policies, one per workload. Receiver and UI
  ingress is restricted to the upstream platform namespace, matched by
  the default `kubernetes.io/metadata.name` label every namespace gets
  (`rossoctl-system`, with `kagenti-system`/`kagenti` kept for
  portability). Postgres ingress is restricted to the
  `data-governance` namespace. PROJECT.md §7 frames v1 as
  unauthenticated cluster-internal: the NetworkPolicy IS the v1
  security boundary.

## Prerequisite: build the image and load it into Kind

The Deployments here reference `data-governance/receiver:latest` and
`data-governance/ui:latest` with `imagePullPolicy: IfNotPresent`. Both tags
are produced from a single repo-root `Containerfile` (see issue #38) — one
image, two tags, two entry points. A fresh Kind cluster has neither tag,
so applying these manifests without first building and loading the image
results in `ErrImagePull` / `CrashLoopBackOff` on the receiver, UI,
interactions-processor, and data-lineage-processor pods.

The `deploy/build-and-load.sh` helper does both steps in one shot:

```sh
./deploy/build-and-load.sh
```

It builds the image from `Containerfile` (multi-stage `uv sync --frozen`
build), tags it as both `data-governance/receiver:latest` and
`data-governance/ui:latest`, and `kind load docker-image`s both tags into
the cluster named `rossoctl`. It **also** materializes the git-LFS model
weights and builds + loads the separate P-classification image
`data-governance/classification:latest` from `Containerfile.classification`
(issue #79 / ADR-0022 — the fat torch + baked-weights image, distinct from
the slim shared image). Override the cluster name with `KIND_CLUSTER=...` and
the tag with `IMAGE_TAG=...` if needed.

> **git-LFS required.** The classification image bakes a ~500 MB git-LFS model
> artifact; `build-and-load.sh` runs `git lfs pull` before building so the real
> weights are baked in, not the ~134-byte pointer (ADR-0023). Install git-lfs
> (<https://git-lfs.com>) if the script errors that it is missing.

The script is idempotent: re-running it rebuilds the image (cache permitting)
and re-loads the tags. The image's compiled Alembic head matches what the
init container will migrate the DB to and what the receiver's startup
schema-version check (PROJECT.md §3 / ADR-0002) will validate.

## Apply

After build + load, apply the manifests:

```sh
kubectl apply -f deploy/k8s/
```

Order matters only for the namespace; everything else is order-independent
and will eventually settle.

The receiver replicas crashloop until Postgres is reachable AND
`alembic_version` matches the head revision compiled into the image. The
init container drives both conditions to true.

## Re-deploying after a code change

For an existing cluster where the manifests are already applied and you
just want the receiver / UI / interactions processor to pick up new code
from `main`, the primary path is `dg.sh component install` — it is
idempotent and re-runs the build + apply + tee + rollout for you:

```sh
git pull --ff-only
./deploy/dg.sh component install       # add --no-build if the images are already loaded
```

This supersedes the manual re-deploy dance for everyday use (and matches the
root `CLAUDE.md` redeploy procedure). The equivalent raw sequence — kept here
for when you need to drive the steps individually — is:

```sh
git pull --ff-only
./deploy/build-and-load.sh
kubectl apply -f deploy/k8s/                                        # usually a no-op; safe to skip if no manifest changes
kubectl -n data-governance rollout restart \
  deployment/data-governance-receiver deployment/data-governance-ui \
  deployment/data-governance-interactions deployment/data-governance-classification \
  deployment/data-governance-data-lineage
kubectl -n data-governance rollout status deployment/data-governance-receiver --timeout=120s
kubectl -n data-governance rollout status deployment/data-governance-ui --timeout=120s
kubectl -n data-governance rollout status deployment/data-governance-interactions --timeout=120s
kubectl -n data-governance rollout status deployment/data-governance-classification --timeout=180s
kubectl -n data-governance rollout status deployment/data-governance-data-lineage --timeout=120s
```

The `rollout restart` is the step that's easy to forget: the manifests
pin `:latest` with `imagePullPolicy: IfNotPresent`, so `kubectl apply`
alone will NOT cycle pods onto the freshly-loaded image — kubelet sees
the same tag it already has and keeps the old running pods. Restarting
the Deployments forces new pods, which then pick up the just-loaded
image from the node's image store.

Postgres (the StatefulSet) does not need restarting — it only holds
data, not code from this repo.

### When the change includes a migration, restart EVERY reader

Not just the component you changed. Every pod runs the migrate init
container to head (ADR-0002), so whichever pod starts first drags the
schema forward under all the others — including pods still running the
previous image.

That is harmless for an additive migration (a new table or column an old
reader never selects). It is **not** harmless for a rename or a drop: the
old code keeps selecting a column that no longer exists and its reads fail
outright. This happened with migration 0013, which renamed
`lineage_metadata.entity_path` to `entities` — applying only
`90-data-lineage.yaml` moved the schema to head while the API pod still
queried `entity_path`, and every lineage read returned
`column m.entity_path does not exist` until the other Deployments were
restarted.

So for a schema change the order above matters: build and load the image
first, then restart every Deployment in the list, and do not apply a
single manifest in isolation expecting only that component to be affected.
If you applied one and reads started failing, restarting the rest is the
fix (see ADR-0028 D10 for why the rename was accepted in this shape).

## Wire the rossoctl collector to our receiver

The data-governance receiver is reachable at
`data-governance-receiver.data-governance.svc.cluster.local:4317` (gRPC),
but the rossoctl otel-collector ships without an exporter pointing here.
That edit lives in the rossoctl repo (issue #42's "Cross-repo
coordination" section). Until that lands upstream, run the helper script
after `kubectl apply -f deploy/k8s/`:

```sh
./deploy/patch-rossoctl-collector.sh
```

It additively patches the live `rossoctl-system/otel-collector-config`
ConfigMap — adding an `otlp/data_governance` exporter and a **dedicated**
`traces/data_governance` pipeline (`otlp → batch → otlp/data_governance`)
that fans the collector's existing OTLP receiver to us — and rolls
`deploy/otel-collector`. data-governance gets its own isolated pipeline;
it does **not** piggyback on any other pipeline (e.g. Phoenix's), so its
span ingestion is independent of the platform's other trace pipelines.
The existing `traces/default` pipeline is left untouched. The script is
idempotent: re-running it after the patch is in place is a no-op. Re-run
it after any cluster recreate or upstream re-apply of the rossoctl
collector ConfigMap.

The agent-examples deploy (travel_advisor) optionally adds a
`filter/a2a_noise` processor into this same `traces/data_governance`
pipeline to drop a2a queue/event-noise spans before we ingest them; this
script tolerates that (`--revert` removes the whole pipeline regardless).

To remove the patch (e.g. before letting upstream own the integration),
pass `--revert` — it removes the `traces/data_governance` pipeline and the
`otlp/data_governance` exporter, restoring the pre-patch collector state:

```sh
./deploy/patch-rossoctl-collector.sh --revert
```

Override the target namespace with `COLLECTOR_NAMESPACE` if your platform
collector lives elsewhere (default `rossoctl-system`).

`--revert` is also idempotent — running it when the exporter is already
absent is a no-op and does not roll the collector.

## UI access via the rossoctl shared Gateway

`60-ui-httproute.yaml` attaches an `HTTPRoute` (in `rossoctl-system`,
where the `shared-gateway-access=true` label lives) to the
`rossoctl-system/http` Gateway, exposing the UI at
**http://dg.localtest.me:8080/**. The route's `backendRefs` target the
`data-governance-ui` Service in `data-governance`; a `ReferenceGrant` in
`data-governance` permits exactly that one cross-namespace edge. This
mirrors how rossoctl-ui, rossoctl-api, keycloak, etc. are exposed on the
same Gateway.

The route is reachable because the Kind cluster maps host port 8080 to
the gateway listener (NodePort 30080 → port 80 inside the cluster). No
TLS, no auth — same v1 unauthenticated-cluster-internal posture as the
NetworkPolicy boundary (PROJECT.md §7). For local dev access, no
`kubectl port-forward` is needed.

## Out of scope for v1 (PROJECT.md §7 / issue #16)

- TLS termination
- Ingress
- External auth / RBAC / mTLS
- Cluster-level monitoring, log aggregation
- Postgres backups, replication, or sizing tuning
- `/metrics` *scraping* — the receiver now exposes Prometheus `/metrics`
  on port 9090 (issue #36), but v1 has no scraper peer in the cluster.
  When a monitoring stack lands, a scraper-namespace ingress rule must
  be added to the receiver NetworkPolicy alongside it.

## Verifying the OTLP path

The "OTLP span lands in spans + surfaces in UI" acceptance criterion is
checked manually against a fresh cluster:

1. `kubectl apply -f deploy/k8s/`
2. Wait for `data-governance-receiver` and `data-governance-ui` pods to
   reach Ready.
3. From a pod inside the platform namespace, send an OTLP span to
   `data-governance-receiver.data-governance.svc:4317` (gRPC) or
   `:4318` (HTTP/protobuf).
4. Open http://dg.localtest.me:8080/ — the trace's listing root should
   appear in the recent-traces view, with the sent span discoverable
   via the trace-tree drill-in. (As a fallback if the rossoctl shared
   Gateway is not present: `kubectl port-forward
   svc/data-governance-ui 8080:8080 -n data-governance` and open
   `http://localhost:8080/`.)

Schema validity and NetworkPolicy structure are exercised automatically
by `tests/deploy/test_manifests.py`.
