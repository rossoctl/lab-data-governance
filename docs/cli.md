# `dg.sh` — Data Governance cluster management

## Purpose

`deploy/dg.sh` is the supported operator entry point for installing the Data
Governance component and activating lineage on Rossoctl-managed workloads.

```text
dg.sh                                              # component status
dg.sh component  [install|uninstall|status]
dg.sh component  install [--no-build|--reuse-classification]
dg.sh namespaces [list]
dg.sh namespace  <ns> [instrument|status] [<entity>]
```

There are no global options. The shim and existing-proxy reconciliation assets
are vendored in `deploy/lineage-attach/`; no Cortex checkout is required.

## Component lifecycle

`dg.sh component install` builds and loads the images, applies the Kubernetes
resources, wires the platform collector tee, and rolls the component workloads.
Use `--no-build` only when the required images are already loaded. Use
`--reuse-classification` when the classification image is in the local container
cache: the script builds and loads the shared receiver/UI image, then loads the
cached classification image into Kind without rebuilding its model or fetching
git-LFS weights. The options cannot be combined.

`dg.sh component uninstall` reverts the collector tee and removes the component.
It deletes the namespace and PVC by default; `--keep-data` preserves the PVC.

`dg.sh component status` is read-only and reports workloads, the receiver
Service, the UI route, and collector-tee state.

## Namespace discovery

`dg.sh namespaces list` lists non-system namespaces enabled for Rossoctl.

## Lineage activation

```sh
./deploy/dg.sh namespace <ns> instrument
./deploy/dg.sh namespace <ns> instrument <entity>
```

The optional entity is selected by `app.kubernetes.io/name`. Without it,
instrumentation selects every agent/tool with a trusted
`rossoctl.io/type=agent|tool` label in the namespace. A namespace with no
trusted agents/tools has nothing to instrument. An explicitly named untrusted
entity fails loudly, with guidance to deploy or import it through Rossoctl.
For namespace-wide activation, the command validates every target first and
then processes tools before agents, alphabetically within each group.

Only the real Rossoctl deployment shape is supported. Every selected entity
must have:

- a trusted `rossoctl.io/type=agent|tool` label;
- a Running pod with an admitted `authbridge-proxy` sidecar;
- the platform `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` contract;
- a lineage-capable AuthBridge plugin catalog;
- a mounted pipeline ConfigMap; and
- exactly one application container whose two-shim image can be attested.

The namespace `authbridge-runtime-config` ConfigMap must exist and be within the
safe reconciliation envelope, no selected entity's AgentRuntime may set
`pluginPreset`, and a run that writes that ConfigMap must preflight every
AgentRuntime target in the namespace.

All selected entities and images are preflighted before the first mutation.
A trusted workload with an Envoy or incomplete proxy fails before mutation.
Bare, legacy component-labelled
workloads are ignored by namespace-wide instrumentation; naming one explicitly
fails before mutation. Data Governance never injects or replaces a sidecar.

Rossoctl admission rebuilds each sidecar's `authbridge-config-<name>` from the
namespace `authbridge-runtime-config` on every pod admission (roll, pod delete,
scale-up), so `instrument` converges the parser and `lineage-telemetry` entries
there, preserving the authentication plugins, and never writes the per-workload
ConfigMap. The lineage entry names no `self_id`; each sidecar reports the last
segment of its admitted client id. Because that write reaches every agent/tool in
the namespace at its next admission, only a namespace-wide run makes it;
`instrument <entity>` requires a namespace that already carries the pipeline.
After preflight, `instrument` rolls each application onto the attested image
with `LINEAGE_PROPAGATE=1` first, because Rossoctl may roll every workload in the
namespace when that ConfigMap changes. It then writes the namespace ConfigMap
when needed, rolls each workload whose pod does not yet run the pipeline, and
waits until the newly admitted sidecar reports it. A re-run on a converged
namespace rolls nothing.

The pipeline then reaches every sidecar admitted in the namespace, including
agents/tools deployed later; `status` reports a later proxy-sidecar workload
`live=no` until `instrument` rolls it onto the shim. Anything that rewrites `authbridge-runtime-config`,
such as a platform `helm upgrade` in a chart-managed namespace, removes it at the
next admission; `status` reports that too.

Activation is non-reversible in v1. Redeploying a workload through Rossoctl
restores its application image, but its sidecar keeps the namespace pipeline;
removing that means editing `authbridge-runtime-config`, which reaches every
agent/tool in the namespace. See
[ADR-0031](adr/0031-non-reversible-namespace-lineage-activation.md) and
[ADR-0033](adr/0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md).

## Namespace status

```sh
./deploy/dg.sh namespace <ns> status
./deploy/dg.sh namespace <ns> status <entity>
```

Status is read-only and includes both trusted and legacy component-labelled
agents/tools, including when both occur in the same namespace. It reports
sidecar presence/type and whether `lineage-telemetry` is wired. On trusted
proxy workloads, it also reports whether the application shim, proxy
environment, namespace `authbridge-runtime-config`, and live pipeline have
converged; a namespace ConfigMap without the pipeline is reported even while
running pods still carry it, because the next pod admission would drop it. It
may truthfully report `sidecar=none`; that state is diagnostic and is not
accepted by `instrument`.

## Typical flow

1. Install Rossoctl and Data Governance.
2. Deploy/import agents and tools through the Rossoctl UI or CLI.
3. Run `./deploy/dg.sh namespace <ns> instrument`.
4. Run the workload.
5. Verify with `./deploy/dg.sh namespace <ns> status` and the Data Governance UI.

Stores and external demo clients are not agents/tools and can remain outside
the sidecar-managed graph.
