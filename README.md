# data-governance

The data-governance extension of Kagenti. See `docs/PROJECT.md` for the v1
architecture and `CONTEXT.md` for the domain glossary.

## Repo layout

```
data_governance/
  db/                 # Layer 1: generic Postgres wrapper (ADR-0005)
    migrate.py        # `python -m data_governance.db.migrate`
    migrations/       # Alembic migrations (raw SQL, no ORM — ADR-0002)
      env.py
      versions/
  processors/
    otlp_receiver/    # P-otel-receiver (Layer 2 + transport, grown over many issues)
tests/
  db/
    test_db.py        # Layer 1 tests against real Postgres
    test_migrations.py # Baseline migration + migrate CLI tests
docs/                 # PROJECT.md, ADRs
alembic.ini
pyproject.toml
```

## Tooling

- **Python 3.11+** (matches modern psycopg 3 / Alembic; Kagenti CI runs 3.11).
- **Package manager / runner: `uv`.** `uv sync` installs runtime + dev
  dependencies into `.venv`. `uv run pytest` to test, `uv run python -m
  data_governance.db.migrate` to apply migrations.
- **Postgres driver: psycopg 3 + psycopg_pool** (per ADR-0005). Async is
  available in psycopg 3 but the v1 db module is sync — async is not yet
  needed and adding it speculatively widens the surface.
- **Migrations: Alembic without SQLAlchemy ORM** (per ADR-0002). Raw SQL via
  `op.execute(...)`. The schema is the source of truth, not Python models.
- **Tests: pytest + testcontainers-python.** Each test session starts a
  throwaway Postgres container; tests get a fresh database per test. No
  mocking of Postgres — that is the whole point of the layered design
  (Layer 1's job is "be a real-Postgres wrapper", and a mocked test of it
  would only assert the mock).

## Running

```bash
# install
uv sync

# apply migrations (assumes DATABASE_URL is set)
export DATABASE_URL='postgresql://user:pass@host:5432/data_governance'
uv run python -m data_governance.db.migrate

# tests (requires Docker / Podman socket for testcontainers)
uv run pytest
```

## Deploying to the local Kind cluster

The full procedure (first-time deploy, re-deploy after a code change,
collector wiring, UI access) lives in
[`deploy/k8s/README.md`](deploy/k8s/README.md). Quick re-deploy from a
clean working tree on `main`:

```sh
git pull --ff-only
./deploy/build-and-load.sh
kubectl apply -f deploy/k8s/
kubectl -n data-governance rollout restart \
  deployment/data-governance-receiver deployment/data-governance-ui
```

The `rollout restart` is required: manifests pin `:latest` with
`imagePullPolicy: IfNotPresent`, so `apply` alone will not cycle pods
onto the newly-loaded image. See `deploy/k8s/README.md` for the full
explanation and the `rollout status` waits.

## Configuration

- `DATABASE_URL` — Postgres DSN. Required for the `migrate` CLI and any
  caller of `data_governance.db`.
- `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` — pool sizing (default 1 / 10).
- `DB_POOL_TIMEOUT` — seconds to wait for a connection from the pool before
  raising `PoolTimeout` (default 30).

## Issue scope

This README and the scaffolding it documents land with issue #2 (Layer 1 db
module + Alembic baseline + migrate CLI). The receiver, retrieval API, and UI
backend are subsequent issues.
