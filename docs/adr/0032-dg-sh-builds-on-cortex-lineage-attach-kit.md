---
status: accepted
---

# `dg.sh namespace instrument` builds on the cortex lineage-attach kit (#852)

`dg.sh namespace <ns> instrument` activates lineage on a namespace's
agents/tools. An earlier design (`docs/cli.md`, and ADR-0031
non-reversible) had `dg.sh` emit the sidecar YAML from its **own vendored
generator**, explicitly "not shelling out to cortex's 762 demo." Cortex **PR
#852** (`authbridge/lineage-attach/`) supersedes #762 with a reviewed,
input-validating, hardened attach kit — the very generator #184 was going to
hand-roll. This ADR decides that `dg.sh` **drives the #852 kit** rather than
vendoring or re-implementing it, and records where the kit's reach stops and
`dg.sh`'s own code begins.

`component install`/`uninstall` (ADR-0031 scope) is unaffected — it is
consumer-side component lifecycle with no relationship to the attach kit.

## Context

The #852 kit is **attach-only** (it never deploys an app) and consists of one
generator (`attach-lineage.sh`: `EMIT=patch`/`EMIT=cm`/`EMIT=undo`, every input
validated, never touches the cluster), a live applier (`sidecar-patch.sh`: six
read-only preconditions → generate all three objects → **`kubectl patch
--dry-run=server`** the merged patch → apply ConfigMap → apply patch → print the
**reverse-patch** back-out line → rollout wait), and a propagate-only Python shim
(`build-otel-shim.sh` + `Dockerfile.otel-shim` + `lineage-propagate-hook.py`,
env-gated by `LINEAGE_PROPAGATE=1`). It follows the same wire contract the
consumer reads (**v1.6.2**, `docs/sidecar-wire-contract.md`, kept byte-identical
with cortex — the earlier design's "v1.5.3" was stale; nothing the derived
tables see changed).

(These are the mechanics of the current kit head, `fbff6753`. An earlier draft of
this ADR — written against `85cf7763` — described five preconditions and a
`rollout undo` back-out; both were superseded by the force-push to `fbff6753`,
and decisions 3 and 4 below record the current facts.)

Two hard facts shape the decision:

1. The kit is **envoy-sidecar-only**. `attach-lineage.sh`'s ConfigMap hardcodes
   `mode: envoy-sidecar` and its patch adds `envoy-proxy` + `proxy-init` via a
   transparent iptables redirect — with `envoy-proxy` emitted as a **native
   sidecar** (an `initContainer` with `restartPolicy: Always`, requiring k8s >=
   1.29). It has **no proxy-sidecar path**.
2. `instrument` acts on entities that have **no sidecar**. An entity that
   already carries a sidecar (proxy or envoy) is out of scope — see the
   revision note below.

> **Revised (2026-09-10):** Decision #2 originally kept a **three**-row table —
> no-sidecar (kit), envoy-sidecar-in-place (kit), proxy-sidecar-in-place
> (`dg.sh`'s own edit) — so the proxy-shaped agent-examples demo could be
> instrumented in place. Live validation
> (`docs/proposals/with-sidecar-live-validation-findings.md`) showed the two
> existing-sidecar rows do not hold: the platform-injected proxy sidecar is the
> **enforcing** sidecar (`jwt`/`token-exchange`) and 401s the demo's
> unauthenticated MCP/A2A calls, and an in-place edit of a **webhook-injected**
> pipeline CM is **clobbered by the operator** (the per-workload
> `authbridge-config-<entity>` CM is Deployment-owned and regenerated on the
> roll `instrument` triggers). `instrument` was therefore narrowed to a
> **kit-only, no-sidecar prerequisite**: any existing sidecar (proxy or envoy)
> is skipped. Decision #2, the tail of Decision #3, Consequences and
> Alternatives below reflect this; Decisions #1, #3 (preconditions), #4 and
> ADR-0031 are unchanged.

## Decision

### 1. `dg.sh` drives the kit; it does not vendor or re-implement it

`namespace instrument` locates the kit under
`$CORTEX_LOCAL_PATH/authbridge/lineage-attach/` (from `--cortex-local-path` /
`CORTEX_LOCAL_PATH`) and calls `sidecar-patch.sh` / `build-otel-shim.sh`
through their documented environment contract. No copy of the kit lives in this
repo.

This **retires the design's original "one script in this repo, without the
operator ever touching the cortex repo" goal.** That goal is replaced by: the
*component* lifecycle is self-contained here; *namespace activation* points at
a cortex checkout the operator provides. The trade was: a vendored copy honors
the self-contained goal but re-owns a reviewed, hardened generator and drifts;
a submodule/subtree couples the consumer build to a cortex fetch; the path
parameter keeps the kit live and drift-free at the cost of requiring a cortex
checkout on disk — which the rossoctl dev cluster already has as a sibling
(`~/rossoctl/cortex`), and which the root `CLAUDE.md` attach recipe already
assumes. The path parameter won.

### 2. The kit owns the one row `instrument` acts on: no-sidecar → inject envoy. Any existing sidecar is skipped.

`instrument` wires lineage onto an entity that has **no sidecar**, and delegates
that entirely to the kit:

- **no-sidecar → inject envoy**: the kit (`sidecar-patch.sh`; the kit's
  ConfigMap *is* the envoy parser-chain + plugin entry) injects an envoy lineage
  sidecar and, for a Python app, bakes + attaches the propagate-only shim.
  `dg.sh` emits no YAML of its own.
- **any sidecar already present (proxy _or_ envoy)** → **skipped.** `instrument`
  detects the sidecar (including a webhook-injected one, via the live-Pod
  fallback — the Deployment template does not carry it) and refuses to touch the
  entity, mutating nothing and continuing to the next. It is out of scope:
  - retrofitting a **proxy** sidecar meant editing its pipeline in place, which
    is clobbered when the sidecar is operator-owned (webhook-injected), and the
    enforcing platform sidecar 401s the demo (findings doc);
  - retrofitting an existing **envoy** sidecar would add the plugin to a
    pipeline `dg.sh` does not own; the sanctioned way to get a dg.sh-ownable
    envoy sidecar is to start from **no sidecar** and let the kit inject one.

`dg.sh` therefore keeps **no generator of its own** — the kit is the sole
emitter. This does not force a sidecar-mode switch (ADR-0031 still holds): an
entity that already runs proxy keeps running proxy; `instrument` simply does not
act on it. The supported lineage path for the agent-examples demo is a bare,
no-sidecar deploy followed by `instrument` (the kit injects envoy) — the
known-working manual attach recipe (`LINEAGE-PROXY-SIDECAR-RECIPE.md`).

### 3. The kit's six preconditions are the source of truth for the kit-owned rows

`dg.sh` does not re-implement the kit's read-only preconditions. As of the
current kit head (`fbff6753`) there are **six**, run in order by
`sidecar-patch.sh`:

1. `require_deployment` — the Deployment exists;
2. `require_envoy_config` — the platform `envoy-config` ConfigMap is present in
   the namespace (the patch mounts it; missing → the pod never starts);
3. `refuse_name_collision` — no container **or init container** already named
   `envoy-proxy`/`proxy-init` (lists merge by name; `envoy-proxy` is itself a
   native-sidecar `initContainer`, so a re-attach is caught here);
4. `refuse_port_collision` — no container declares 9090/15123/15124;
5. `refuse_volume_collision` — **no volume already named
   `envoy-config`/`authbridge-runtime`** (volumes merge by name too, so the
   merge would silently repoint the owner's mount) — this is the sixth check the
   force-push to `fbff6753` added on top of the earlier five;
6. `require_app_container` — `APP_CONTAINER` names a real container (a strategic
   merge would otherwise ADD a stub container by that name).

`dg.sh` adds only the checks the kit does not make: component installed +
collector tee present. (The former proxy-row-only checks — a proxy precondition
and the `egressEnforcement: none` warning — went away with the proxy row; see
the Decision #2 revision.)

### 4. Kit compatibility is verified by symptom, in two complementary layers

Rather than statically assert the kit's cortex ref is new enough, compatibility
is caught by two layers that between them cover the whole failure window:

- **The kit's pre-apply `--dry-run=server` guard** (its own "version guard").
  Before the first write, `sidecar-patch.sh` runs `kubectl patch
  --dry-run=server` on the fully-merged object: the server validates the merge
  without persisting it, so a bad merge, an admission-webhook rejection, missing
  `deployments/patch` RBAC, and a cluster older than k8s 1.29 (which rejects the
  native-sidecar fields `startupProbe`/`restartPolicy` on an init container) all
  fail **before** anything is applied. No version parsing is needed.

- **`dg.sh`'s post-apply crash-loop watch.** The dry-run cannot see a *runtime*
  config↔binary mismatch, so `dg.sh` watches the rollout each attach triggers
  and detects a **crash-looping sidecar** — the `unknown plugin
  "lineage-telemetry"` / config-key-skew boot-crash surfaces as
  `CrashLoopBackOff` within seconds. On a failed rollout or crash-loop, `dg.sh`
  prints the sidecar container log and **surfaces the kit's printed back-out
  line verbatim** (see below), fails loud, and does not proceed to the next
  entity.

These layers are **complementary, not redundant**: the dry-run catches
pre-apply rejections; the crash-loop watch catches post-apply boot failures.
`dg.sh` keeps the crash-loop guard *and* relies on the kit's dry-run.

The back-out is a **reverse patch, not a `rollout undo`** (this changed with
`fbff6753`). `sidecar-patch.sh` prints, before its rollout wait:

```
>> back out: kubectl -n <ns> patch deploy/<d> --type strategic -p '<undo>' && kubectl -n <ns> delete cm authbridge-lineage-config-<d>
```

Because `envoy-proxy` is a native sidecar (an `initContainer` with
`restartPolicy: Always`), a whole-revision `rollout undo` would restore too much
— it would take the owner's later changes with it — so the kit's own comment
says a `rollout undo` is explicitly **not** the back-out. The reverse patch
deletes, by name, exactly what the attach added (and restores the app image it
replaced). `dg.sh` **captures that printed line and surfaces it verbatim** on a
crash-loop rather than synthesizing a `rollout undo`. This catches *any*
incompatibility (stale kit, ahead kit, wrong image, config skew) by its actual
symptom.

## Why

- **The kit is better than a vendored generator would be.** #852 is
  input-validating (RFC-1123 names, port ranges, enumerated switches, refuses
  free-form with quote/backslash/whitespace; every refusal `exit 2`),
  security-hardened (capabilities dropped, `proxy-init` adds back only
  NET_ADMIN/NET_RAW, envoy non-root 1337), and review-gated. Re-implementing
  that in `dg.sh` would duplicate reviewed surface area and drift from it.
- **The path parameter matches the cluster's reality.** Cortex is already a
  sibling checkout; the existing ad-hoc attach recipe already reaches into it.
  Formalizing that as `--cortex-local-path` is less friction than either a
  vendored copy the operator must keep in step or a submodule the build must
  fetch.
- **Symptom-based verification is more robust than a version floor.** The
  version-skew failure is a runtime config↔binary mismatch (see the root
  `CLAUDE.md` and the config-version-skew note); a SHA or version-marker check
  is brittle and can pass while still mismatching. Catching the crash-loop
  catches the whole class.

## Consequences

- `dg.sh namespace instrument` requires a cortex checkout; the other verbs do
  not touch cortex. Missing `--cortex-local-path`, or a kit absent under it, is
  a refuse-and-mutate-nothing preflight.
- `dg.sh` carries **no generator of its own** — the kit is the sole emitter for
  the one row `instrument` acts on (no-sidecar → inject envoy). An entity that
  already has a sidecar is skipped, not instrumented (Decision #2, revised).
- The propagate-only shim (`build-otel-shim.sh`) becomes part of the
  no-sidecar Python flow `dg.sh` drives, not an operator afterthought: for an
  uninstrumented Python agent it is the difference between one trace and N
  fragments (root `CLAUDE.md` § 4). An app that instruments itself is refused
  the shim by the kit's bake interlock and gets capture only.
- ADR-0031's two decisions (non-reversible v1; never switch sidecar mode)
  survive unchanged — the kit is additive-attach and forces no mode switch, and
  skipping an existing-sidecar entity likewise touches nothing.
- Because `instrument` only ever acts on a **no-sidecar** entity, re-running it
  on a namespace it already instrumented is a clean no-op per entity: each now
  carries an envoy sidecar and is skipped ("already has an envoy sidecar").

## Alternatives considered

- **Vendor a pinned copy of the kit into `deploy/`** — rejected: honors the
  self-contained goal but re-owns reviewed code and drifts; the dev cluster
  already has cortex on disk, so the goal it protects is not load-bearing here.
- **git submodule / subtree of cortex** — rejected: couples the consumer build
  to a cortex fetch and complicates the stacked #181–#185 branch chain, for no
  gain over a path the operator already has.
- **`dg.sh` keeps its own generator, adopting the kit's YAML shape** —
  rejected: duplicates hardened surface area. Originally retained for the proxy
  row the kit cannot produce; the Decision #2 revision drops that row entirely
  (the proxy in-place edit was clobbered by the operator on a webhook-injected
  sidecar — findings doc), so `dg.sh` now keeps no generator.
- **Instrument an existing sidecar in place (proxy edit / envoy plugin-add)** —
  rejected on live evidence. The proxy in-place edit is clobbered when the
  sidecar is operator-owned (regenerated on the next roll), and the enforcing
  injected sidecar 401s the demo; adding the plugin to an existing envoy
  pipeline means editing config `dg.sh` does not own. The supported route is a
  bare no-sidecar deploy → `instrument` lets the kit inject a dg.sh-ownable
  envoy sidecar. (This is the "narrow to no-sidecar-only" that an earlier draft
  of this ADR had rejected in favor of the three-row table; the findings doc
  reversed that.)
- **Narrow v1 to envoy-only, drop the proxy row** — originally rejected
  (reverses ADR-0031's "don't force a mode switch" and breaks the proxy-based
  demo path; PR 760 makes proxy a tested route worth keeping). **Superseded by
  the Decision #2 revision:** the concern was misplaced — narrowing to
  no-sidecar-only forces *no* mode switch (an existing proxy entity keeps
  running proxy; it is simply skipped), and the proxy in-place edit never
  actually worked on the injected demo sidecar (clobbered by the operator).
  So the narrowing was adopted, for a different and stronger reason than this
  bullet had weighed.
- **Assert the kit's cortex ref meets a version floor** — rejected in favor of
  symptom-based crash-loop detection: a ref/marker check is brittle and
  narrower than the failure it guards.
