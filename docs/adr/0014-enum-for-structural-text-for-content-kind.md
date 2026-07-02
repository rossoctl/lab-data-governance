# Postgres ENUM for the structural enums, TEXT for content_kind

The `P-interactions` derived tables carry four closed-set columns:
`entities.kind`, `entity_spans.role`, `interaction_spans.role`, and
`interaction_payloads.content_kind`. The `spans` table stores its one
closed-set column (`spans.kind`) as `TEXT` (ADR-0002 era; migration `0002`
deliberately relaxed it to nullable for flexibility). We **diverge** from
that precedent for `P-interactions`: the three **structural** enums become
Postgres `ENUM` types, while `content_kind` stays `TEXT`.

## The rule

- `CREATE TYPE entity_kind AS ENUM ('user','client','agent','tool','llm','service')`
- `CREATE TYPE entity_span_role AS ENUM ('discovered_via','identified_via')`
- `CREATE TYPE interaction_span_role AS ENUM ('anchor','info','connector')`
- `interaction_payloads.content_kind` is `TEXT`, validated in code by the
  **Payload extraction rule**.

## Why

- **The `spans.kind` precedent does not transfer.** `spans.kind` is an
  *external* enum (OTEL's `SpanKind`) written by a *semantically unaware*
  receiver (`P-otel-receiver`) that must accept whatever lands, including
  `SPAN_KIND_UNSPECIFIED` → `NULL` (migration `0002`). A DB constraint that
  could reject a span is actively wrong there. `P-interactions` is the
  opposite: it **defines** these enums itself and **is** the classifier, so
  a value outside the set is a *processor bug*, not data to tolerate. A
  Postgres ENUM rejects it at write — the strongest guard for the columns
  that form the spine of the derived graph (`interaction_span.role` drives
  ADR-0008 territory ownership; `entity.kind` drives the seven-kind
  identity model).
- **The three structural enums are domain-fixed.** Six entity kinds, two
  entity-span roles, three interaction-span roles — fixed by the domain,
  effectively never churning. The cost of an ENUM (a migration to add a
  value) is rare *and* is exactly ADR-0002's stated value: every schema
  change is a reviewable, ordered artifact.
- **`content_kind` is different — it churns.** New **Content kinds** are
  added as the payload classifier matures, and `unknown` already provides
  the open-world escape (analytics measure classifier coverage by
  `content_kind = 'unknown'`). Forcing a migration per new kind would buy
  rigidity where we want to evolve freely, and `ALTER TYPE ... ADD VALUE`
  carries Alembic-transaction friction. TEXT + code enforcement fits.

## Consequences

- The split is along "is this set stable?": ENUM where fixed, TEXT where
  evolving. A reader seeing ENUM here but `TEXT` on `spans.kind` will ask
  why — this ADR is the answer (different owner, different churn, different
  semantic-awareness of the writing processor).
- Adding a **Content kind** is a code change (no migration); adding an
  entity kind or a role is a migration (`ALTER TYPE ... ADD VALUE`). The
  CONTEXT.md **Content kind** term reflects this ("adding a kind is a code
  change").
- Migrations use raw `op.execute("CREATE TYPE ...")` (ADR-0005: Alembic
  without the SQLAlchemy ORM), so no ORM enum coupling.
