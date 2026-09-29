# Existing-proxy lineage activation kit

This directory contains the application-image shim and AuthBridge pipeline
reconciler used by `deploy/dg.sh namespace <ns> instrument`.

Agents and tools must first be deployed or imported through Rossoctl. Admission
must provide an `authbridge-proxy` sidecar, its authenticated pipeline
ConfigMap, the proxy environment, and the trusted `rossoctl.io/type` label.
Data Governance does not inject a proxy or Envoy sidecar into a bare workload.

## Components

- `build-otel-shim.sh` builds an application image containing propagation and
  turn-span startup hooks.
- `lineage-propagate-hook.py` propagates trace context across supported Python
  clients when `LINEAGE_PROPAGATE=1`.
- `rossoctl_turnspan.py` and `rossoctl_turnspan.pth` create the server turn span
  required to keep one user turn in one trace.
- `attest-otel-shim.py` verifies that a built or running image contains both
  hooks.
- `reconcile-existing-proxy.py` preserves the admission-owned authentication
  pipeline while converging its parser and `lineage-telemetry` configuration.

## Contract

Instrumentation is transactional at namespace scope: all selected workloads,
live pods, proxy contracts, plugin catalogs, ConfigMaps, and shim images are
validated before the first mutation. A missing or non-AuthBridge sidecar fails
closed with instructions to deploy the workload through Rossoctl.

The status command remains diagnostic and may truthfully report
`sidecar=none`; that state is not instrumentable.
