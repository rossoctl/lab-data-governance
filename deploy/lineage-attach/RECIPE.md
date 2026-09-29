# Lineage activation recipe

Deploy or import the agents and tools through Rossoctl, then run:

```sh
./deploy/dg.sh namespace <namespace> instrument
./deploy/dg.sh namespace <namespace> status
```

To scope activation to one workload:

```sh
./deploy/dg.sh namespace <namespace> instrument <entity>
```

Do not patch a sidecar into a bare Deployment. If `instrument` reports that the
workload is not Rossoctl-managed or lacks an admitted AuthBridge proxy, correct
the deployment in Rossoctl and retry. The command makes no mutation in that
case.

On success, run the workload and confirm that `status` reports a trusted proxy,
wired lineage, and `live=yes`.
