---
status: accepted
---

# Namespace lineage activation is additive and non-reversible

## Decision

`dg.sh namespace <ns> instrument [<entity>]` activates lineage only for
Rossoctl-managed agents and tools that already have an admitted AuthBridge proxy
sidecar. It preserves authentication and sidecar ownership, adds the two-shim
application image, and reconciles the existing proxy pipeline.

The command has no reset operation. Reversal is performed by redeploying the
workload through Rossoctl. An explicitly named workload without the trusted
Rossoctl identity is rejected before mutation. Namespace-wide instrumentation
selects only trusted workloads, then rejects any selected workload without the
expected proxy sidecar before mutation.

`status` remains read-only and reports trusted and legacy component-labelled
workloads, including `sidecar=none` when applicable.

## Consequences

- Data Governance cannot create a platform identity or sidecar.
- All selected workloads are preflighted before any is changed.
- Rossoctl remains the authority for authentication and sidecar lifecycle.
