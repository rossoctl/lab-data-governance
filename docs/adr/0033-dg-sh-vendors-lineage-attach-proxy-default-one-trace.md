---
status: accepted
---

# Reconcile existing Rossoctl proxies for one-trace lineage

## Context

Agents and tools created through the Rossoctl UI already have authenticated
AuthBridge proxy sidecars. Injecting a second, auth-free proxy is not a realistic
platform deployment and creates conflicting ownership and routing behavior.

A complete user turn also needs application trace-context propagation and a
turn span, so pipeline reconciliation alone is insufficient.

## Decision

`dg.sh namespace <ns> instrument [<entity>]` supports one deployment shape:

1. Rossoctl owns the workload identity, AuthBridge sidecar, authentication
   plugins, and admission-generated ConfigMap.
2. Data Governance preflights every selected workload and fails closed unless
   it has the trusted label, a live AuthBridge proxy, the expected proxy
   environment, a lineage-capable plugin catalog, and a safely reconcilable
   ConfigMap.
3. Data Governance builds and attests one application image containing both
   propagation and turn-span hooks, then enables it with
   `LINEAGE_PROPAGATE=1`.
4. After the application rollout, Data Governance reconciles the newly admitted
   ConfigMap, preserving authentication while converging the parser and
   `lineage-telemetry` configuration.
5. The command verifies the live hot-reloaded pipeline. It does not inject or
   replace a sidecar.

The preflight is namespace-transactional: all validation and image builds finish
before the first Deployment or ConfigMap mutation.

## Consequences

- Bare agent/tool Deployments must be deployed or imported through Rossoctl
  before instrumentation.
- Stores and external demo clients are not agents/tools and may remain
  sidecar-free.
- `status` may report absent or non-proxy sidecars, but `instrument` refuses
  those states.
- The vendored kit contains only shim-build, attestation, and existing-proxy
  reconciliation assets.
