# Layer-1 LISTEN/NOTIFY session connections

The P-interactions processor (ADR-0007) drains `spans` by `seq` on a poll
loop. Polling means derived rows lag by up to a poll interval. To make the
drain wake promptly on a new span (issue #71) we use Postgres
`LISTEN`/`NOTIFY`: a database trigger fires `pg_notify` on insert, and the
processor `LISTEN`s for it.

`LISTEN` needs a connection the existing `db` surface cannot give it. The
`db.transaction()` context manager (ADR-0005) hands out **pooled,
autocommit-off** connections that are checked back into the pool on context
exit. `LISTEN` registration is **session-scoped** — it lives only as long as
the connection is held — and the consumer must sit on that connection in
**autocommit** mode to receive notifications as inserts commit. A pooled,
short-lived, autocommit-off connection is the wrong shape on every axis.

We chose to add a small generic **`db.listen(channel, dsn)`** helper to
Layer 1: it opens its own `psycopg.connect(dsn, autocommit=True)` outside the
pool, runs `LISTEN <channel>`, and yields a handle whose `wait(timeout)`
blocks for a notification (or the timeout). The connection is held for the
caller's `with` block — i.e. the processor's lifetime — and closed on exit.

## Relationship to ADR-0005

ADR-0005 says "Layer 1 is intentionally complete from day one; growth happens
at Layer 2." That rule is about **domain operations** — `write_span`,
`get_spans`, the things that know about the schema and the §6 semantics. Those
belong in Layer 2 and must not leak into the generic wrapper.

`db.listen()` is **not** a domain operation. It is **connection
acquisition / lifecycle** — exactly what ADR-0005 already lists under Layer 1
("Owns: the connection pool … connection acquisition … any future driver-level
concerns"). A long-lived session connection for `LISTEN` is the same *category*
as the pool: a way of obtaining and managing a psycopg connection, ignorant of
spans, the channel's meaning, or the processor. The channel name
(`dg_spans_inserted`) and the decision to listen at all stay with the caller
(the processor); Layer 1 only knows "open a connection, `LISTEN` on this
identifier, hand back a wait()."

So this amends ADR-0005's "complete from day one" framing — Layer 1 grows by
exactly one connection-lifecycle primitive — without contradicting its core
rule that **domain** capabilities live in Layer 2.

## The helper

- `db.listen(channel, dsn)` — context manager. Validates `channel` as a bare
  SQL identifier (`LISTEN` cannot parameterize the channel name, so the value
  is interpolated; rejecting anything but `[A-Za-z_][A-Za-z0-9_]*` keeps that
  safe). Opens a dedicated autocommit connection, runs `LISTEN <channel>`,
  yields a `Listener`, closes the connection on exit.
- `Listener.wait(timeout)` — returns `True` if at least one notification
  arrived within `timeout`, `False` on timeout. The notification *count* and
  *payload* are irrelevant: the consumer's reaction is always "drain whatever
  is past the cursor," which coalesces any burst. Connection-class errors
  propagate so the caller can fall back to its poll backstop.

## Consequences

- **The drain stays correct without the notification.** `LISTEN` is layered on
  the already-correct poll loop (ADR-0007); the poll interval remains the
  backstop. A dropped/missed notification, a severed listen connection, or a
  cluster without the trigger only costs latency, never progress — the
  processor falls back to pure polling.
- **One extra Postgres connection per processor**, outside the pool, held for
  the process lifetime. Negligible at v1 scale (the processor runs a single
  replica — see `deploy/k8s/70-interactions.yaml`).
- **Layer 1 is no longer "complete from day one."** It now carries one
  connection-lifecycle primitive beyond the pooled transaction. It stays
  strictly generic: no domain concept enters the `db` module.
- **The trigger lives in the database, not the receiver** (ADR-0007 — the
  receiver stays unaware of derived consumers). Migration
  `0005_spans_notify_trigger` owns the `dg_notify_spans()` function and the
  statement-level `AFTER INSERT ON spans` trigger.
