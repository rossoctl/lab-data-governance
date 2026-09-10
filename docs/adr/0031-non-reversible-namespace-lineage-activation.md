---
status: accepted
---

# Non-reversible, mode-preserving namespace lineage activation in `dg.sh`

`dg.sh namespace <ns> instrument` switches on lineage telemetry for every
agent/tool in a namespace: it wires the cortex `lineage-telemetry` plugin into
each entity's AuthBridge sidecar pipeline so its traffic produces the
facts-only spans this component consumes. This ADR records two coupled
decisions about that verb — that it is **non-reversible** (there is no `reset`
in v1) and that it is **additive-only and never changes a namespace's sidecar
mode** — and why. The full script design is in `docs/cli.md`; this
ADR captures the *why* and the boundary. `component install`/`uninstall` is a
separate, fully reversible concern and is out of scope here.

## Context

Lineage requires the `lineage-telemetry` plugin (cortex PR 761) to run inside
an AuthBridge sidecar, and its cross-pod parenting depends on the sidecar's
listener propagating a `dg-parent` tracestate header — fixed for both
`ext_proc` (envoy-sidecar) and `forwardproxy` (proxy-sidecar) by cortex PR 760.
On a rossoctl cluster a given agent/tool is in one of three states:

- **no sidecar** — the workload carries `rossoctl.io/inject: disabled` (e.g.
  the agent-examples demo agents), so the operator injects nothing;
- **proxy-sidecar** — the cluster default injected mode (`authbridge-proxy`);
- **envoy-sidecar** — the opt-in mode (`envoy-proxy` + `proxy-init`).

The sidecar **mode** is resolved by the operator's injection webhook from the
per-namespace ConfigMap `authbridge-runtime-config` (`mode:` field), falling
back to a deprecated pod annotation, then the cluster default `proxy-sidecar`.
The webhook fires on **pod CREATE only**, so a mode change takes effect only
after the affected pods are recreated.

The intended use is a **demo / observability** flow on a long-lived Kind dev
cluster, ending at "observe the trace in the data-governance UI." Namespaces
there are disposable; the natural way to "start over" is to delete or redeploy
the workloads.

> **How the plugin is wired is decided separately.** This ADR is about the
> *reversibility and mode-preservation* of `instrument`. **ADR-0032** decides
> that the wiring itself is done by driving the cortex lineage-attach kit
> (PR #852) — located via `--cortex-local-path` — and, on live validation, was
> revised to wire lineage onto **no-sidecar entities only** (any entity that
> already has a sidecar, proxy or envoy, is skipped). The two ADRs are
> compatible: the kit is additive-attach and forces no mode switch, so both
> decisions below hold regardless of which entities `instrument` ends up wiring.

## Decision

### 1. Activation is additive-only and never switches sidecar mode

`instrument` only ever **adds** — it injects a lineage sidecar where there is
none, and skips any entity that already has one (ADR-0032). It never edits,
replaces, or removes an existing sidecar, and in particular never flips a
namespace from `proxy-sidecar` to `envoy-sidecar`.

### 2. Activation is non-reversible in v1

There is no `reset` / un-instrument verb. To undo activation, delete or
redeploy the namespace's workloads. `dg.sh namespace <ns> status` reports, per
entity, whether a sidecar is present, its type, and whether the plugin is
wired, so the one-way action remains observable.

## Why

**Why no mode switch (decision 1).** Switching a namespace to `envoy-sidecar`
is a namespace-wide, security-adjacent change with a real footgun: a workload
using `tlsBridgeMode: enabled` **loses its TLS bridge** under envoy-sidecar
(the setting is rejected/no-op there). It also requires recreating every
injected pod in the namespace (CREATE-only webhook) and mutates shared operator
state (the namespace runtime ConfigMap). `instrument` sidesteps all of this: it
only injects a lineage sidecar where there is none and leaves every existing
sidecar untouched (ADR-0032), so it never alters how any workload's traffic is
secured — which makes "non-reversible" (decision 2) safe, because the only thing
not scripted-undoable is the newly-injected lineage sidecar, not a change to an
existing workload's security posture.

**Why non-reversible (decision 2).** A precise reversible un-wire is the
genuinely hard, fiddly part of the design: strategic-merge patches do not
cleanly un-merge, so a faithful `reset` needs marker annotations recording
exactly what was added and a per-entity inverse for the injected sidecar —
significant surface area for a v1 whose target is a disposable dev cluster
where `kubectl delete namespace` / redeploy is the expected reset. Because
activation is additive and touches no shared operator state, deferring the
scripted undo costs little: nothing about the cluster is irreversibly altered.
The upstream attach tooling `dg.sh` builds on (the cortex lineage-attach kit,
PR #852, superseding the #762 demo; ADR-0032) is itself an additive attach
whose only back-out is the manual **reverse-patch** line it prints (a
strategic-merge patch that deletes by name exactly what the attach added, then a
ConfigMap delete — not a `rollout undo`; ADR-0032 decision 4) — so there is no
scripted teardown to port from it either.

## Consequences

- `dg.sh` v1 is small: `component` (reversible) + `namespaces list` +
  `namespace instrument`/`status`. No reverse-patch logic, no marker-annotation
  machinery.
- On a **proxy-sidecar** entity the plugin only sees outbound calls if egress
  is actually captured (`egressEnforcement != none`, or the app honours
  `HTTP_PROXY`). `instrument` **warns** loudly when it is not; envoy-sidecar
  captures transparently via `proxy-init`, so no warning there.
- A namespace whose agents need lineage but sit in `proxy-sidecar` with egress
  capture off gets a clear warning rather than a silent partial graph — but the
  operator must arrange egress capture (or envoy-sidecar) themselves; `dg.sh`
  will not change the mode for them.
- If a future version wants true reversibility or mode switching, it is an
  additive change (a new `reset`/`--switch-mode` path) that supersedes this
  ADR; nothing here blocks it.

## Alternatives considered

- **Switch proxy-sidecar namespaces to envoy-sidecar during activation** —
  rejected: bakes in the TLS-bridge footgun and a namespace-wide pod
  recreation, and is unnecessary now that PR 760 makes proxy-sidecar work.
- **Reversible activation with marker-based `reset`** — deferred, not rejected:
  worth building when there is a non-disposable target, but disproportionate
  for a v1 dev-cluster demo flow.
- **Refuse proxy-sidecar entirely, envoy-only** — rejected: with PR 760 (and
  its `forwardproxy` header-diff test) proxy-sidecar is a tested propagation
  path, so refusing it would force needless mode switches.
