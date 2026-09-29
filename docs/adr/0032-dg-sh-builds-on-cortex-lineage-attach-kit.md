---
status: superseded
---

# The external Cortex sidecar-attachment design is retired

This ADR originally described attaching a new sidecar to a bare Deployment.
That deployment model no longer represents Rossoctl/UI-created agents and tools
and has been removed.

ADR-0033 now defines the supported contract: the workload is deployed through
Rossoctl first, AuthBridge is admitted by the platform, and Data Governance
reconciles that existing proxy without replacing its authentication pipeline.
