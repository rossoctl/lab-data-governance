# Existing AuthBridge proxy integration

`dg.sh instrument` augments Rossoctl-managed workloads without taking ownership
of their sidecars.

The operation has two phases:

1. Preflight every selected entity. Require a trusted Rossoctl label, a running
   pod with one admitted `authbridge-proxy`, the platform proxy environment, a
   lineage-capable plugin catalog, a safely reconcilable ConfigMap, and an
   attested two-shim application image.
2. After every preflight succeeds, roll each application onto the shim image,
   reconcile the admitted proxy ConfigMap, and verify the live hot-reloaded
   pipeline.

The boundary is deliberate: Rossoctl owns workload identity, authentication,
and sidecar lifecycle; Data Governance owns only application trace propagation
and lineage-pipeline convergence. Bare workloads are rejected before mutation.

See [ADR-0033](../../docs/adr/0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md).
