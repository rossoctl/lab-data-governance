# Data Governance

## Executive Summary

Today data is flowing in the platform without any centralized governance — which results in enforcement with very limited context. In this epic we provide a comprehensive solution for data visibility, risk, policies and enforcement across the platform. The epic will provide the necessary context — across agents, sessions, users, and trajectories — for making data aware decisions.

## Overview
Data-Aware Governance & Security for Agentic Platforms to deliver comprehensive, data-aware governance and security capabilities natively within the platform, ensuring secure data lifecycles and robust risk mitigation across complex AI agent ecosystems.

Core Capabilities & Strategic Pillars:

- Traceability at Scale (Collection & Enrichment)
Systematically collect, classify, and enrich data flow traces across the entire agentic platform to establish complete, scalable observability.

- Advanced Threat Detection (Analysis & Monitoring)
Deploy sophisticated data flow analysis technology capable of monitoring multiple agent trajectories and sessions. This continuous analysis operates over configurable time windows to proactively detect and mitigate critical risks, specifically targeting potential data breaches and data corruption.

- Autonomous Policy Enforcement (Data Barriers)
Implement robust Data Barriers designed to enforce strict, policy-driven dataflow constraints. These barriers will operate autonomously to govern the entire data lifecycle, specifically controlling how information is accessed, transformed, and retained within the system.

Implementation & Validation:

The developed security mechanisms will be fully integrated into Kagenti. This deployment will explicitly showcase the platform's ability to mitigate both OWASP Agentic Application Risks and the OWASP Data Security Top 10.

Proof of Value:
The operational success and security efficacy of this integration will be actively demonstrated alongside 1 to 2 "Client 0" internal early adopters.

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

## Loading a trace or a fixture

`tools/load_trace.py` replays a captured span fixture (a snapshot of `spans`
rows, e.g. `travel_agent_II.json`) through the live OTLP receiver over gRPC or
HTTP, exercising the full ingest path.
(It rebuilds the OTLP protobuf by hand —
inverting `otlp_receiver/translate.py` — so fields the SDK exporter would drop
(`kind`, `Status`, `events`, `links`, `scope`, the `otlp` envelope) survive.
Unlike `load_fixture.py`, it talks only OTLP and never touches the database.)

Send it to the `data-governance-receiver` pod. In the Kind deployment its
OTLP ports aren't on the host, so port-forward first:

```sh
kubectl -n data-governance port-forward svc/data-governance-receiver 4317:4317 4318:4318 &
python tools/load_trace.py travel_agent_II                 # gRPC :4317 (default)
python tools/load_trace.py travel_agent_II --transport http  # HTTP :4318
python tools/load_trace.py travel_agent_II --dry-run         # build + count only
python tools/load_trace.py travel_agent_II --now             # shift times to now
python tools/load_trace.py travel_agent_II --reid --now      # fresh trace_id, shows in last-hour view
```

`--now` shifts all timestamps by one offset so the trace ends now (durations
preserved), making it read as just-arrived in the UI.

`--reid` rewrites the trace onto a fresh random `trace_id` + `span_id`s before
sending (printing the new id), so re-loading a fixture you have already ingested
reads as genuinely new data — a plain re-replay is a no-op because span upserts
are finalization-only and the interactions cursor has already passed those seqs.

Takes a full path, `*.json` path, or bare stem. Override the target with
`--endpoint` (gRPC wants a bare `host:port`, HTTP wants a full URL):

```sh
python tools/load_trace.py travel_agent_II --endpoint otel-collector:4317
python tools/load_trace.py travel_agent_II --transport http --endpoint http://otel-collector:4318
```

On a transport or receiver error (e.g. gRPC `UNAVAILABLE`, or HTTP 503 when
the receiver's DB is down — ADR-0003) the tool exits non-zero with a
`retryable, no spans persisted` message rather than a raw traceback.

A fixture is a JSON array of span objects, one per span, mirroring the `spans`
columns. Required per span: `trace_id` (32-hex), `span_id` (16-hex), `name`,
`started_at` (ISO-8601). Optional: `parent_id` (16-hex; omit/`null` for a root),
`kind` (`INTERNAL`/`SERVER`/`CLIENT`/`PRODUCER`/`CONSUMER`), `ended_at`,
`error` (`true`/`false`/`null`), `status_message`, `service_name`,
`attributes` (object), `events`/`links` (arrays), `scope` (`{name, version,
attributes}`), `resource_attributes` (object), `otlp` (envelope: `flags`,
`trace_state`, `dropped_*_count`). DB-assigned columns (`seq`, `arrival_seq`,
`observed_at`) may be present but are ignored on replay.

## Issue scope

This README and the scaffolding it documents land with issue #2 (Layer 1 db
module + Alembic baseline + migrate CLI). The receiver, retrieval API, and UI
backend are subsequent issues.
