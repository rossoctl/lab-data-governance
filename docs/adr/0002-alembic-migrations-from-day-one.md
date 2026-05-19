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
