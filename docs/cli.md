# `dg.sh` — data-governance cluster management script

## Purpose

Give the data-governance component a first-class **install / uninstall** story
on a rossoctl cluster, and a way to **activate lineage telemetry** on the
agents and tools in a namespace so their traffic shows up in the
data-governance UI — all drivable from **one script in this repo**.

The *component* lifecycle is fully self-contained here. The *namespace
activation* step drives the **lineage-attach kit** (originally cortex PR #852)
**vendored into this repo** at `deploy/lineage-attach/` — the reviewed,
input-validating producer-side attach tooling ships as `dg.sh`'s own local
copies, so no cortex checkout is required for any verb
([ADR-0033](adr/0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md),
which supersedes [ADR-0032](adr/0032-dg-sh-builds-on-cortex-lineage-attach-kit.md)
and retires the `--cortex-local-path` flag that pointed at an external checkout).

Today (before this script):

- install is the ad-hoc `deploy/build-and-load.sh` + `kubectl apply -f
  deploy/k8s/` + a separate manual `deploy/patch-rossoctl-collector.sh`;
- there is **no uninstall** (removal is fully manual);
- the *producer* side (attaching the AuthBridge `lineage-telemetry` plugin to an
  agent/tool) exists in cortex — first as the install-only
  `authbridge/demos/lineage/` demo (PR 762), now **superseded by the
  lineage-attach kit** (PR #852), a curated, attach-only kit with its own
  generator, live applier, and propagation shim.

`dg.sh` folds the component lifecycle into three verbs
(`install`/`uninstall`/`status`) and adds namespace activation that **drives the
vendored kit** from here, in the consumer's repo.

## Background: how the pieces fit

- **Producer** — cortex PRs **760** (listener header parity: `extproc` **and**
  `forwardproxy` now propagate every plugin header mutation, tracestate
  included; verified by the new `forwardproxy/server_headerdiff_test.go`) +
  **761** (the `lineage-telemetry` plugin — two facts-only OTel spans per HTTP
  exchange, `lineage.*` attributes, a `dg-parent` **tracestate** stamp for
  cross-pod parenting). The plugin is registered into **both** the
  `authbridge-envoy` and `authbridge-proxy` binaries, and with 760 in, its
  parenting works on **both** sidecar modes.
- **Attach tooling** — the reviewed **kit** that wires the plugin onto a running
  Deployment (originally cortex PR **#852**, depends on #761, follows the same
  wire contract), now **vendored into this repo** at `deploy/lineage-attach/` and
  extended with a **proxy** applier + generator (ADR-0033). `dg.sh namespace
  instrument` drives its own local copies rather than emitting its own YAML — see
  the `instrument` section and
  [ADR-0033](adr/0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md).
  On a **no-sidecar** entity `instrument` injects a lineage sidecar — a
  lineage-only **proxy** by default, **envoy** when the namespace is already
  envoy-configured; an entity that **already has a sidecar** gets
  `lineage-telemetry` **appended in place** (best-effort, ADR-0033 owner-split).
- **Consumer** — this repo's `feat/interactions-sidecar-algorithm` branch reads
  those spans (wire contract **v1.6.2** — `docs/sidecar-wire-contract.md`, kept
  byte-identical with cortex; the `parent.source` tracestate/wire/none minting
  is the v1.6 shape the branch already consumes, see
  `tests/processors/interactions/test_sidecar_minted_entry.py`), derives
  interactions/entities, and needs **no receiver change** (spans arrive over
  the existing OTLP path). Its only production manifest change is
  `INTERACTIONS_ALGORITHM=sidecar`.
- **Transport** — `deploy/patch-rossoctl-collector.sh` (already exists, with
  `--revert`) tees the platform OTel collector's traces to
  `data-governance-receiver.data-governance.svc.cluster.local:4317`.

So the span path is **agent sidecar → platform collector → (tee) → receiver →
interactions processor → UI**. `dg.sh` owns the two ends: standing up the
consumer, and switching the plugin on at the source.

## CLI

```
dg.sh                                              # → component status (no args)
dg.sh component  [install|uninstall|status]
dg.sh namespaces [list]
dg.sh namespace  <ns> [instrument|status] [<entity>]
```

There are **no global options**: the lineage-attach kit is vendored into this
repo (`deploy/lineage-attach/`), so `instrument` needs no path to an external
checkout. The `--cortex-local-path` / `CORTEX_LOCAL_PATH` option that pointed at
a cortex checkout was **retired** (ADR-0033); passing it is now an unknown-option
usage error.

The optional `<entity>` on `instrument` and `status` scopes the verb to a
single agent or tool (by `app.kubernetes.io/name`); omitted, the verb acts on
**every** agent/tool in the namespace. An `<entity>` that does not resolve to
an agent/tool in `<ns>` is an error (fail loud, do not no-op).

`instrument` runs `dg.sh`'s own vendored kit under `deploy/lineage-attach/`
(overridable in tests via `DG_LINEAGE_ATTACH_DIR`) and refuses — mutating
nothing — if that vendored kit is missing or incomplete (a broken-checkout
invariant). No verb requires a cortex checkout.

### `dg.sh component` — REVERSIBLE

The data-governance component itself: Postgres, receiver, UI, the three
processors, the UI HTTPRoute, and the collector tee.

- **`install`** (idempotent):
  1. build + kind-load images via `deploy/build-and-load.sh` (skip with
     `--no-build` when images are already loaded);
  2. `kubectl apply -f deploy/k8s/`;
  3. `deploy/patch-rossoctl-collector.sh` (tee the platform collector to the
     receiver);
  4. `kubectl rollout restart` the receiver/ui/interactions deployments (the
     load-bearing `:latest`/`IfNotPresent` step), then `rollout status`.
- **`uninstall`** (the inverse):
  1. `deploy/patch-rossoctl-collector.sh --revert`;
  2. delete the `rossoctl-system` HTTPRoute (+ the `data-governance`
     ReferenceGrant);
  3. `kubectl delete namespace data-governance` **by default** (takes the
     Postgres PVC with it). `--keep-data` instead deletes the workloads
     individually and preserves the PVC.
- **`status`**: are the deployments present/ready, and is the collector tee
  wired?

`component` stays fully reversible — it is cheap (the tee already has
`--revert`) and useful for redeploys and cleanup.

### `dg.sh namespaces list`

List **user** namespaces: those labelled `rossoctl-enabled: "true"`, excluding
`kube-*` and `*-system`.

### `dg.sh namespace <ns> instrument [<entity>]` — NON-REVERSIBLE (v1)

Activate lineage for the agents/tools in `<ns>` — **all** of them, or the
single `<entity>` when named. **Additive-only, and it never changes a
namespace's sidecar mode** (see ADR-0031 for why non-reversible, and why no
mode switch).

Selection prefers the trusted operator label `rossoctl.io/type=agent|tool`.
When any such Deployment exists, only that trusted set is eligible; clients,
stores, and other workloads are excluded. A namespace with no trusted labels
uses `app.kubernetes.io/component=agent|mcp-tool` for the legacy #239 flow.
When `<entity>` is given, it must belong to the selected set.
`instrument` dispatches each entity by its **current sidecar state**, all
auto-detected, no flag (the ADR-0033 owner-split, which supersedes ADR-0032's
kit-only no-sidecar-only contract):

| Entity's current state | Action | Owner |
|---|---|---|
| **no sidecar**, namespace **not** envoy-configured (`rossoctl.io/inject: disabled`, e.g. the agent-examples demo agents) | inject a lineage-only **proxy** sidecar (auth-free, `mode: proxy-sidecar`), + for a Python app the two-shim image | **dg.sh proxy applier** (`sidecar-patch-proxy.sh` / `build-otel-shim.sh`) |
| **no sidecar**, namespace **already** envoy-configured | inject an **envoy** lineage sidecar (`mode: envoy-sidecar` + `lineage-telemetry`), + the two-shim image | **vendored envoy applier** (`sidecar-patch.sh` / `build-otel-shim.sh`) |
| sidecar present, **no** `lineage-telemetry` in its pipeline | **append** `lineage-telemetry` in place (auth left as-is), best-effort + verify-after-roll | `dg.sh` in-place append |
| sidecar present, `lineage-telemetry` **already** wired | **no-op** (idempotent) | — |

"Already envoy-configured" is detected from the namespace — the presence of the
platform `envoy-config` ConfigMap — not a flag. The default for a bare, ad-hoc
namespace (the travel_advisor demo has no `envoy-config`) is therefore the
**proxy** sidecar: it carries only the parsers + `lineage-telemetry` — **auth-free**
(no `jwt-validation` / `token-exchange` / mTLS), so it does not 401 the demo's
unauthenticated calls — and captures egress transparently via an **include-only
iptables allowlist** (A2A `8080` + MCP `8000` by default; every other port stays
direct — fail-safe). Both injected sidecars are **native sidecars** (an
`initContainer` with `restartPolicy: Always`, so they are up before the app
container and stay up for the pod's life; requires k8s >= 1.29). The vendored
envoy applier's `attach-lineage.sh` hardcodes `mode: envoy-sidecar`; the proxy
applier's `attach-lineage-proxy.sh` emits `mode: proxy-sidecar`.

**Instrumenting an existing sidecar in place.** An earlier design (ADR-0032)
*skipped* any entity that already had a sidecar, because live validation found
in-place editing risky: the platform-injected proxy sidecar can be the
**enforcing** sidecar (`jwt`/`token-exchange`) that 401s the demo's unauthenticated
MCP/A2A calls, and an in-place edit of a **webhook-injected** pipeline ConfigMap is
**clobbered by the operator** (the per-workload CM is Deployment-owned and
regenerated on the roll `instrument` triggers).

**ADR-0033 (accepted 2026-09-14) revised this to the owner-split above**:
`instrument` now **appends** the `lineage-telemetry` plugin to an existing
sidecar's pipeline (leaving its auth plugins exactly as-is — additive, never
strip), as a **best-effort** step. It **verifies after the roll** that the plugin
is actually live and **warns loudly** when the operator clobbered it, or when the
existing pipeline is enforcing (so lineage will record 401s for unauthenticated
callers). It also flips the injected default for a *no-sidecar* entity from envoy
to the auth-free **proxy** sidecar (envoy only when the namespace is already
envoy-configured), and vendors the attach capability into `deploy/lineage-attach/`
so `dg.sh` needs no cortex checkout (`--cortex-local-path` retired). See ADR-0033
for the full rationale; the durable-in-place alternatives (operator-rendered /
skip-injected) are recorded there as future work.

**Issue #256 supersedes the existing-sidecar sequence for trusted Rossoctl
proxy workloads.** Before the first workload mutation, `instrument` validates
every admitted proxy and producer catalog, validates every mounted ConfigMap,
and builds/attests every distinct required application shim. It then updates
only the application container image and `LINEAGE_PROPAGATE=1`, waits for
rollout and webhook reinjection, resolves the new pod's ConfigMap, and
canonically reconciles both pipelines while preserving JWT-validation and
token-exchange. AuthBridge must hot reload the change and expose it from
`/v1/pipeline`; no rollout occurs after the ConfigMap edit.

The plugin points `otel_endpoint` at the platform collector
(`otel-collector.rossoctl-system.svc.cluster.local:4317`); the existing
component tee carries the spans to the receiver. This is the kit's own default
(`attach-lineage.sh`'s `OTEL_ENDPOINT` default is exactly this), so the
no-sidecar row needs no override.

**The propagation shim.** Capture alone records every hop but cannot
attribute an app's *outbound* calls to the inbound that caused them — only code
inside the request carries the `traceparent` through, and an uninstrumented
Python app (the agent-examples demo agents) does not. This is exactly the
fragmented-trace failure the root `CLAUDE.md` § 4 documents. The kit bakes a
propagate-only OpenTelemetry shim for that case (`build-otel-shim.sh` →
`APP_CONTAINER`/`APP_IMAGE` flips `LINEAGE_PROPAGATE=1`). `dg.sh namespace
instrument` therefore **also drives the shim bake/attach** for a no-sidecar
Python entity: it is not optional decoration but the difference between one
trace and N fragments for the demo target. For an entity that instruments
itself, the kit's bake interlock refuses the shim and `dg.sh` attaches capture
only.

**How `dg.sh` reaches the kit.** `dg.sh` runs the **vendored** kit scripts under
`deploy/lineage-attach/` (overridable in tests via `DG_LINEAGE_ATTACH_DIR`) and
calls `sidecar-patch.sh` / `build-otel-shim.sh` per entity, passing `NAMESPACE`,
`DEPLOY`, `SELF_ID`, `APP_CONTAINER`, `APP_IMAGE`, `SIDECAR_IMAGE`,
`PROXY_INIT_IMAGE`, `OTEL_ENDPOINT` through the kit's documented environment
contract. The kit is vendored **as-is** into this repo (ADR-0033 Decision #1,
which reverses ADR-0032's drive-an-external-checkout arrangement).

**Preflights** (fail loud, never guess):

- **the vendored kit is intact** — `deploy/lineage-attach/{attach-lineage,`
  `sidecar-patch,build-otel-shim}.sh` exist and are executable; otherwise refuse,
  mutating nothing (a broken-checkout invariant, not a missing-path error);
- the component must be installed and the collector tee present (a `dg.sh`-side
  check the kit does not make — it points anywhere it is told);
- a plugin-bearing sidecar image must be resolvable on the cluster — the cortex
  PRs are unmerged, so until they land this means a locally-built
  `authbridge-envoy` (and `proxy-init`) image loaded into kind, passed to the
  kit as `SIDECAR_IMAGE`/`PROXY_INIT_IMAGE`. Compatibility is verified in **two
  complementary layers**, not by asserting a version:
    - **the kit's own pre-apply guard.** Before any write, `sidecar-patch.sh`
      runs `kubectl patch --dry-run=server` on the fully-merged object — "the
      version guard too" in the kit's words: the server validates the merge
      without persisting it, so a bad merge, an admission-webhook rejection,
      missing `deployments/patch` RBAC, and a cluster older than k8s 1.29
      (which rejects the native-sidecar fields `startupProbe`/`restartPolicy`
      on an init container) all fail *before* the first write, with nothing
      applied;
    - **`dg.sh`'s post-apply crash-loop watch.** The dry-run cannot see a
      *runtime* config↔binary mismatch, so `dg.sh` **watches the rollout the
      attach triggers and detects a crash-looping sidecar** (the `unknown plugin
      "lineage-telemetry"` / `DisallowUnknownFields` boot-crash surfaces as
      `CrashLoopBackOff` within seconds). On a failed rollout or a crash-looping
      pod, `dg.sh` prints the `envoy-proxy`/`authbridge-proxy` container log and
      **surfaces the kit's printed back-out line verbatim** — a strategic-merge
      **reverse patch** (`kubectl -n <ns> patch deploy/<d> --type strategic -p
      '<undo>' && kubectl -n <ns> delete cm authbridge-lineage-config-<d>`) that
      `sidecar-patch.sh` prints *before* the wait, so it is available even when
      the wait fails. `dg.sh` captures that line rather than synthesizing its
      own: the kit's back-out is **not** a `rollout undo` — because `envoy-proxy`
      is a **native sidecar** (an `initContainer` with `restartPolicy: Always`),
      a whole-revision undo would restore too much (the owner's later changes
      too), so the reverse patch deletes by name exactly what the attach added.
      `dg.sh` then fails loud and does **not** proceed to the next entity.

  Together the dry-run catches pre-apply rejections and the crash-loop watch
  catches post-apply boot failures — between them they catch *any*
  incompatibility (a stale kit, an ahead kit, a wrong image, config-key skew) by
  its symptom, not by guessing at versions (ADR-0032 Decision #4; see also the
  version-skew failure mode in the root `CLAUDE.md`);
- the applier's own **read-only preconditions** (the envoy applier
  `sidecar-patch.sh`, in order: `require_deployment` (Deployment exists);
  `require_envoy_config` (platform `envoy-config` ConfigMap in the namespace);
  `refuse_name_collision` (no container/init-container already named
  `envoy-proxy`/`proxy-init`); `refuse_port_collision` (no container declares
  9090/15123/15124); `refuse_volume_collision` (no volume already named
  `envoy-config`/`authbridge-runtime` — volumes merge by name, so the merge would
  silently repoint the owner's mount); `require_app_container` (`APP_CONTAINER`
  names a real container)) are the source of truth for the **injected** no-sidecar
  rows `instrument` drives — `dg.sh` does **not** re-implement them. The proxy
  applier `sidecar-patch-proxy.sh` mirrors these for the proxy path, minus
  `require_envoy_config` (the auth-free proxy mounts no `envoy-config`) and
  guarding the proxy's own ports (8081/8082/9091). (These collision guards mean a
  stray attempt to inject onto an entity that already has a sidecar would fail
  cleanly, but `dg.sh` never reaches an applier for an already-sidecarred entity
  — it **appends in place** instead.)

There is **no `reset`** in v1. To undo, delete/redeploy the namespace's
workloads (the natural escape hatch on a dev cluster). Activation is additive
and never mutates shared operator state (no mode switch), so there is nothing
irreversible about the *cluster* — only that `dg.sh` does not script the
un-wire. (For the kit-owned rows, the kit already prints a precise
**reverse-patch** back-out line — a strategic merge that deletes by name exactly
what the attach added and restores the app image it replaced, then deletes the
ConfigMap — so a manual back-out is one printed line away. This is a reverse
patch, **not** a `rollout undo`: the kit deliberately does not use `rollout
undo` because `envoy-proxy` is a native sidecar and a whole-revision undo would
restore too much. `dg.sh` surfaces that printed line on a failure but still
treats activation as one-way at the verb level — it does not script the undo.)

### `dg.sh namespace <ns> status [<entity>]`

Read-only. For every agent/tool in `<ns>` — or just `<entity>` when named —
report:

- **sidecar presence** — does the pod run an AuthBridge sidecar;
- **sidecar type** — `proxy` / `envoy` / `none`;
- **lineage** — is `lineage-telemetry` wired into its pipeline, reported as the
  per-entity `lineage=yes/no` token. This is the no-marker idempotency signal
  (ADR-0033 decision 5): `instrument` places no marker, so a wired pipeline is
  how a re-run knows an entity is already activated.
- **live** — for trusted proxy workloads, also verifies the shim image,
  `LINEAGE_PROPAGATE=1`, admission proxy environment, pod/sidecar readiness,
  canonical mounted ConfigMap, and live AuthBridge pipeline. Drift is shown as
  `live=no` with the failed condition.

Detection inspects **both** the pod-spec's `containers` and its `initContainers`:
the kit's `envoy-proxy` is a native sidecar (an `initContainer`), so a
`containers`-only scan would report a fully-instrumented pod as `sidecar=none`.
When the Deployment template shows no sidecar, `status` also falls back to the
live Pod (a webhook-injected sidecar exists only there); a transient/RBAC failure
of that pod read is inconclusive — it keeps the template verdict and continues,
never aborting the run.

This is the partner to a non-reversible `instrument`: it answers "did
activation take, and on what shape of sidecar?" — for the whole namespace or a
single entity.

## Typical operator scenario

1. Install rossoctl on the cluster.
2. `dg.sh component install`.
3. Install agents/tools (via the rossoctl UI, or an `agent-examples`-style
   deploy — **install only**, do not run yet).
4. `dg.sh namespace <ns> instrument` on the namespace holding those workloads
   (the kit is vendored into this repo — no cortex checkout to point at).
5. Run the agents.
6. Observe the resulting traces in the data-governance UI
   (`http://dg.localtest.me:8080`).

## Out of scope for v1

- `reset` / un-instrument (namespace activation is one-way; ADR-0031).
- Switching a namespace's sidecar **mode** (`proxy-sidecar` ↔ `envoy-sidecar`) of
  an *existing* sidecar — activation never touches operator mode state. It injects
  a sidecar onto a **no-sidecar** entity (proxy by default; envoy when the
  namespace is already envoy-configured) and **appends** `lineage-telemetry` to an
  existing sidecar's pipeline, but never mode-switches one already in place.
- **Durable** in-place activation of an operator-injected sidecar — the append is
  **best-effort**: an operator that regenerates the per-workload ConfigMap on the
  roll clobbers it (`instrument` verifies after the roll and warns loudly rather
  than claiming a success it did not achieve). The durable alternatives
  (operator-rendered plugin / skip-injected) are ADR-0033 future work.
- Any change to the cortex *producer* (the `lineage-telemetry` plugin / sidecar
  image lives in cortex); `dg.sh` drives the vendored attach kit and emits no
  YAML of its own.
- Keeping the vendored kit forever in lock-step with cortex upstream — the copy
  under `deploy/lineage-attach/` is owned here now (ADR-0033); it is re-synced
  deliberately, not automatically tracked.

## Reused building blocks

- `deploy/build-and-load.sh` — image build + kind load (`KIND_CLUSTER=rossoctl`,
  images `data-governance/{receiver,ui,classification}:latest`).
- `deploy/patch-rossoctl-collector.sh` — the collector tee, with `--revert`
  (`RECEIVER_ENDPOINT` already defaults to the receiver's gRPC service DNS).
- `deploy/k8s/*.yaml` — the component manifests.
- **`deploy/lineage-attach/` (vendored from cortex PR #852, extended for ADR-0033)**
  — the producer-side attach kit `namespace instrument` drives, now shipping in
  this repo: `sidecar-patch.sh` (the **envoy** live applier + read-only
  preconditions + a pre-apply `--dry-run=server` version guard + the printed
  reverse-patch back-out line), `sidecar-patch-proxy.sh` (the **proxy** live
  applier — its ADR-0033 sibling), `attach-lineage.sh` / `attach-lineage-proxy.sh`
  (the envoy / proxy generators), `build-otel-shim.sh` (the two-shim bake).
  `dg.sh` emits no YAML of its own; it injects onto no-sidecar entities (proxy by
  default, envoy when the namespace is envoy-configured) and **appends** lineage
  in place onto an entity that already has a sidecar.
