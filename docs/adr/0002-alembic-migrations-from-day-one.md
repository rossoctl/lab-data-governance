# Alembic migrations from day one, run as a k8s init container

The receiver could have shipped with ad-hoc declarative DDL on
startup (`CREATE TABLE IF NOT EXISTS ...`, `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS ...`) — sufficient for v1's additive schema
changes and trivial to implement. We chose instead to introduce
**Alembic** from the first commit, run via a single `migrate` CLI
that a Kubernetes init container invokes before the receiver
container starts.

## Why

- **Retrofitting a migration history later is painful.** Once a
  schema has evolved through ad-hoc DDL across multiple deployments,
  reconstructing a coherent migration sequence requires forensic
  work. Starting with Alembic's version table from day one means
  every schema change is a reviewable, ordered artifact.
- **Non-additive changes will eventually arrive.** Alembic handles
  renames and type changes natively; ad-hoc `IF NOT EXISTS` DDL
  doesn't. Building the muscle memory now is cheaper than switching
  later.
- **Init container separates concerns.** The receiver process opens
  the OTLP socket and trusts the schema is at head. Migrations
  run in their own container with their own image lifecycle, so a
  long migration doesn't delay receiver readiness past what k8s
  itself reports, and the receiver's `/healthz` (§5.1) doesn't
  need to gate on migration state.

## Considered alternatives

- **Ad-hoc declarative DDL on receiver startup.** Lighter, no
  dependency. Rejected: doesn't give us versioned history,
  retrofit cost grows with schema age.
- **Migrations run in the receiver process on startup.** Simpler
  deployment (one container) but couples receiver readiness to
  migration completion and forces `/healthz` to know about
  migrations. Rejected for k8s; the same `migrate` CLI is still
  the manual fallback for non-k8s deployments.
- **A different Python migration tool (yoyo-migrations) or a
  language-foreign tool (golang-migrate, sqitch).** Alembic wins on
  Python ecosystem familiarity and the ability to run in-process if
  ever needed. We use it without SQLAlchemy ORM coupling — raw SQL
  via `op.execute(...)` — so we get the framework without buying
  into the ORM.

## Consequences

- **Every schema change is a migration file** in the receiver's
  `migrations/versions/` directory, code-reviewed alongside the
  change that needs it.
- **Deployments require ordering.** k8s deployments use an init
  container; non-k8s deployments require the operator to run
  `python -m data_governance.receiver migrate` before starting the
  receiver. Documented in §3.
- **`/healthz` stays simple** — "OTLP open + Postgres reachable,"
  no migration-state knowledge.
- **The receiver image carries Alembic.** The init container reuses
  the receiver image with a different command, so there's only one
  image to build and version.
- **The receiver does a startup schema-version check.** On startup,
  the receiver reads `alembic_version.version_num` from Postgres and
  compares it to the head revision compiled into the image. On
  mismatch, the receiver logs an actionable error and exits non-zero;
  k8s crash-loops the pod, which surfaces in `kubectl get pods`
  immediately. This is belt-and-suspenders against deployment
  errors (operator deploys a new image without updating the migrate
  job, or runs the wrong image against the wrong DB). `/healthz`
  (§5.1) stays "OTLP open + Postgres reachable" — it does not gate
  on the version check, because by the time the receiver answers
  `/healthz` the check has already passed.
- **v1 migrations are additive; replicas tolerate head or one-ahead.**
  During a rolling update with an additive migration, the new init
  container runs migrate to the new head before any new receiver pod
  starts; old receiver pods continue serving against the head schema
  (additive changes don't break them). Non-additive migrations
  (column renames, type changes, drops) require a maintenance
  window: scale receiver to zero, run migrate, scale back up.
- **A failed migration crash-loops the init container.** New receiver
  pods never become Ready; existing pods continue serving until
  they themselves restart. There is no automatic rollback in v1 —
  operator intervention is required to fix the migration (revert
  the image, fix the migration file, redeploy). This is the
  documented failure mode; v1 does not invest in automatic
  rollback machinery.
- **Multi-replica deploys race the migrate init container.** Each
  pod runs its own init container; all of them attempt
  `alembic upgrade head` against the same Postgres. Alembic uses
  Postgres advisory locks during migration, so only one wins and
  the others observe "already at head" and exit cleanly.
  Correctness is fine. Operational note: heavy migrations
  serialize across replicas, so a 30s migration on a 2-replica
  deploy adds ~30s to the slowest pod's startup, not 60s — but
  startup is no longer parallel during migration.
