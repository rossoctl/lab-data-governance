# Layered Postgres access: Layer 1 db wrapper + Layer 2 domain functions

Both the receiver and the retrieval API talk to Postgres. v2 will
add processors that also read and write. The naive choices are
"every component opens its own connections and writes its own SQL"
or "an ORM (SQLAlchemy) with declarative models." Both are wrong
for v1.

We chose a **two-layer architecture**: a thin generic Postgres
connection/transaction wrapper (Layer 1) on top of psycopg 3 +
psycopg_pool, with domain-specific functions (Layer 2) that
consume it. No ORM. No SQL escape hatch on the read API.

## The layers

**Layer 1 — `db` module.** A thin abstraction over psycopg 3 +
psycopg_pool. Owns:

- The connection pool (one per receiver process, one per UI backend
  process; both reach the same Postgres StatefulSet).
- A `with db.transaction() as tx: tx.execute(sql, params)` context
  manager. On clean exit, the transaction commits and the
  connection returns to the pool. On exception, it rolls back.
- Standard psycopg-shaped surface: `tx.execute`, `tx.execute_many`,
  `tx.fetch_one`, `tx.fetch_all`. Parameterized queries throughout;
  no string concatenation.

This module is generic — it knows nothing about spans, the schema,
the OTLP envelope, or the listing-root rule. It is the kind of code
written once and changed only when the underlying driver changes.

**Layer 2 — domain functions.** Built on Layer 1. Owns:

- `write_span(span)` — receiver's per-span insert, including the
  conditional finalization rule (ADR-0004). Opens one Layer 1
  transaction per call.
- `get_spans(...)` — the typed retrieval method (§6). Read-only;
  the UI backend's only entry point. Opens a Layer 1 transaction
  per call (REPEATABLE READ for paginated consistency).
- `record_blocked_span(pattern)` — increments
  `blocked_span_counts(pattern)` per §3.1.
- Future processor functions, added when their use cases land.

Layer 2 is where the domain's terminology lives — `Span` objects,
listing roots, the §3 schema's structure, the §6 method semantics.
It calls into Layer 1 for the actual SQL execution.

## Why

- **One canonical place where Postgres is mentioned.** Every reader
  and writer goes through Layer 1; nobody else opens a connection
  or constructs SQL. The schema, the SQLSTATE classification (ADR-0003),
  the connection-pool sizing, and any future driver-level concerns
  all live in one module.
- **The receiver and the future processor speak the same language.**
  v2 processors get exactly the same Layer 1 + Layer 2 surface
  that the receiver uses today. No "v2 unified API" needs to be
  designed against a single caller; the abstraction has been
  validated by a second consumer (the UI backend's read path) since
  v1.
- **No ORM coupling.** The schema is the source of truth, not Python
  models. Migrations are hand-written SQL via Alembic
  (ADR-0002). Layer 2 functions translate SQL rows into `Span`
  objects directly — no model registry, no session lifecycle, no
  unit-of-work-of-changes pattern. The schema can evolve without
  forcing a model refactor.
- **Generic Layer 1 is reusable.** The receiver, retrieval API, and
  any future processor share one connection pool wrapper. Without
  this layer, each component would re-implement transaction
  management, connection acquisition, and parameter binding
  slightly differently.

## Considered alternatives

- **SQLAlchemy ORM.** Rejected: forces declarative models that
  duplicate the schema, couples Python types to schema evolution,
  and hides the SQL we want to read. The migration tooling
  (Alembic, ADR-0002) is used **without** the ORM precisely
  because the ORM's value proposition doesn't match a verbatim-OTLP
  storage model.
- **Bare psycopg in each component.** Rejected: every consumer
  re-implements transaction management; the receiver and UI backend
  end up with subtly different connection lifecycles and
  retry-on-disconnect logic.
- **A SQL escape hatch on the retrieval API.** Considered and
  rejected. The original PROJECT.md draft proposed `execute_sql`
  as a library-level escape hatch for ad-hoc analytics. v1 has no
  caller for it (the UI backend uses only typed methods; processors
  don't exist yet), and exposing one would lock callers into
  Postgres-specific SQL. Internal code that genuinely needs raw SQL
  uses Layer 1 directly.
- **A unified domain-typed UoW API for processors.** Considered as a
  v2 future, in which processors would write `tx.write_span(...)` /
  `tx.get_spans(...)` against a typed transactional handle. Rejected:
  the v1 design instead promotes Layer 1 to a first-class internal
  abstraction that any processor can use directly. Processors that
  want typed operations call Layer 2 functions; processors that
  want bespoke SQL use Layer 1. No separate v2 API is needed.

## Consequences

- **The receiver image carries psycopg 3, psycopg_pool, and Alembic.**
  All three are runtime dependencies. The image is shared between
  the receiver container and the migrate init container (ADR-0002).
- **One connection pool per process.** Each receiver replica (Q19:
  2 replicas in v1) maintains its own pool with a default ~10
  connections. The UI backend has its own pool. Total Postgres
  connections at v1 scale: ~20–30, well within Postgres defaults.
- **Layer 2 functions return domain objects, not raw rows.**
  `get_spans` returns `Span` objects (per §6); `write_span` takes a
  `Span` (or its constructor arguments). Layer 1 returns raw rows;
  the translation layer is in Layer 2.
- **Adding a new domain operation is a Layer 2 function, not a new
  Layer 1 capability.** Layer 1 is intentionally complete from day
  one; growth happens at Layer 2.
- **Postgres lives in its own StatefulSet** in the data-governance
  namespace, reached via Service. This decision is forced by
  ADR-0002 (init-container migrate cannot reach a sidecar Postgres
  on `localhost`) but stands on its own merits — separating
  database lifecycle from receiver lifecycle, allowing independent
  scaling and PVC sizing.
