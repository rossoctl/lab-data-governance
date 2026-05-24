# Data-governance Kubernetes manifests (v1)

These manifests stand up the v1 deployment topology pinned by PROJECT.md
§1 / §7:

| File                          | Resources                                                   |
| ----------------------------- | ----------------------------------------------------------- |
| `00-namespace.yaml`           | `Namespace data-governance`                                 |
| `10-postgres-secret.yaml`     | `Secret data-governance-postgres`                           |
| `20-postgres.yaml`            | `Service` + `StatefulSet` for Postgres                      |
| `30-receiver.yaml`            | `Service` + `Deployment` for the OTLP receiver              |
| `40-ui.yaml`                  | `Service` + `Deployment` for the UI backend                 |
| `50-networkpolicy.yaml`       | `NetworkPolicy` for receiver, UI, and Postgres ingress      |

## Topology summary

- **Receiver Deployment.** 2 replicas. Init container runs
  `python -m data_governance.db.migrate` to head; main container runs
  `python -m data_governance.processors.otlp_receiver`. PROJECT.md §3 +
  ADR-0002.
- **Receiver Service.** ClusterIP. Ports: 4317 (OTLP gRPC), 4318
  (OTLP HTTP/protobuf and `/healthz`), 9090 (Prometheus `/metrics`,
  PROJECT.md §5.1; issue #36 wired `MetricsServer` into the receiver
  entry point). The v1 NetworkPolicy does NOT open 9090 — v1 has no
  monitoring peer; adding a scraper ingress rule is a v2 concern.
- **Postgres StatefulSet.** Single replica, 10Gi PVC. Same namespace as
  receiver. Production-grade Postgres operations (backup, replication,
  sizing) are out of scope for v1 (PROJECT.md §1, §5).
- **UI backend Deployment.** Single replica running `python -m
  data_governance.api` on port 8080. Serves both `GET /spans` and the
  static UI shell.
- **NetworkPolicy.** Three policies, one per workload. Receiver and UI
  ingress is restricted to the upstream Kagenti namespace, matched by
  the default `kubernetes.io/metadata.name=kagenti` label every
  namespace gets. Postgres ingress is restricted to the
  `data-governance` namespace. PROJECT.md §7 frames v1 as
  unauthenticated cluster-internal: the NetworkPolicy IS the v1
  security boundary.

## Apply

```sh
kubectl apply -f deploy/k8s/
```

Order matters only for the namespace; everything else is order-independent
and will eventually settle.

The receiver replicas crashloop until Postgres is reachable AND
`alembic_version` matches the head revision compiled into the image. The
init container drives both conditions to true.

## Out of scope for v1 (PROJECT.md §7 / issue #16)

- TLS termination
- Ingress
- External auth / RBAC / mTLS
- Cluster-level monitoring, log aggregation
- Postgres backups, replication, or sizing tuning
- `/metrics` *scraping* — the receiver now exposes Prometheus `/metrics`
  on port 9090 (issue #36), but v1 has no scraper peer in the cluster.
  When a monitoring stack lands, a scraper-namespace ingress rule must
  be added to the receiver NetworkPolicy alongside it.

## Verifying the OTLP path

The "OTLP span lands in spans + surfaces in UI" acceptance criterion is
checked manually against a fresh cluster:

1. `kubectl apply -f deploy/k8s/`
2. Wait for `data-governance-receiver` and `data-governance-ui` pods to
   reach Ready.
3. From a pod inside the Kagenti namespace, send an OTLP span to
   `data-governance-receiver.data-governance.svc:4317` (gRPC) or
   `:4318` (HTTP/protobuf).
4. `kubectl port-forward svc/data-governance-ui 8080:8080 -n
   data-governance` and open `http://localhost:8080/` — the trace's
   listing root should appear in the recent-traces view, with the
   sent span discoverable via the trace-tree drill-in.

Schema validity and NetworkPolicy structure are exercised automatically
by `tests/deploy/test_manifests.py`.
