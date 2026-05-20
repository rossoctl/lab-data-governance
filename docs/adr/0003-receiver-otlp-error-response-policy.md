# Receiver OTLP error-response policy by Postgres SQLSTATE

The `P-otel-receiver` writes each accepted span as its own Postgres
transaction (§3). Some inserts will fail. The OTLP response shape lets
us either fail the whole batch (retryable) or report individual rows
in `ExportTracePartialSuccess.rejected_spans` (sender drops them).
The choice — what happens on failure, per failure kind — is wire-visible
and load-bearing for upstream operators, so it's pinned here rather
than buried in receiver code.

We considered three policies:

- **Silent drop and count.** Increment `db_errors_total{kind=...}`,
  ack the OTLP request as success, move on. Simplest; matches
  "v1 is demo deployment." Cost: silent data loss the OTLP sender
  cannot detect.
- **All-or-nothing.** Any per-span failure → fail the whole batch
  with a retryable status. Sender retries. Cost: one genuinely
  poison span (e.g. a real integrity violation that will never
  succeed) wedges its batch into infinite retry until the sender's
  max-retries kicks in.
- **Partial success.** Commit what commits, report `rejected_spans`
  for what didn't. Sender drops the rejected ones. Cost: no
  automatic retry for transient failures — exactly the case where
  retry would help.

We chose a **fourth option that splits by SQLSTATE**: connection
errors fail the batch retryable; integrity errors that won't be
fixed by a retry go into `rejected_spans`; PK conflicts aren't
errors at all and are tallied separately.

## The mapping

| SQLSTATE | Class | Response | Metric |
|---|---|---|---|
| `08*` (connection exception) | connection | batch fails retryable (gRPC `UNAVAILABLE` / HTTP 503) | `db_errors_total{kind=connection}` |
| `23505` on `(trace_id, span_id)` PK | duplicate | INSERT skipped, span counted as duplicate, OTLP success | `spans_duplicate_total` |
| `23xxx` other than the above | integrity | that span in `rejected_spans` with `error_message`, OTLP partial-success | `db_errors_total{kind=integrity}` |
| `54000` program_limit_exceeded, `22001` string_data_right_truncation, `22023` invalid_parameter_value | too-large / malformed | that span in `rejected_spans` with `error_message`, OTLP partial-success | `db_errors_total{kind=integrity}` |
| Anything else | other | batch fails retryable (conservative — we don't know if retry helps) | `db_errors_total{kind=other}` |

Pool exhaustion / connection-acquire timeouts map to `kind=connection`
and the retryable batch-failure path, even though they don't carry a
SQLSTATE per se.

## Why

- **Connection failures are transient by definition.** Postgres is
  unreachable now, but will be later. Retrying the batch is exactly
  what we want; the sender's collector retry queue is the right
  buffer.
- **Integrity failures and too-large rows won't get better with
  retry.** Reporting them as `rejected_spans` tells the sender
  "drop these specific spans," which is honest. The
  `error_message` carries enough detail for the sender's logs.
- **PK conflicts on `(trace_id, span_id)` are normal.** OTLP retries
  redeliver the same spans after network blips; under §3's
  finalization rule (ADR-0004) they may also represent a partial
  followed by an idempotent partial-retry. Counting them as errors
  would drown real signal.
- **`kind=other` is conservatively retryable.** If we don't know
  what a SQLSTATE means, we shouldn't ask the sender to drop the
  span. Retry is the safer default; if it turns out the error
  truly is permanent, the sender's max-retries policy bounds the
  cost.

## Considered alternatives

- **Silent drop and count.** Rejected: the operator finds out about
  data loss only after the fact, by reading our metrics. The OTLP
  sender's collector has no way to know its spans were dropped.
- **All-or-nothing batch failure on any error.** Rejected: a single
  poison span blocks an entire batch indefinitely. Mixes "transient"
  and "permanent" into one indistinguishable retry loop.
- **Pure partial-success on every failure.** Rejected: removes the
  retry path that connection errors specifically need. A Postgres
  blip would silently drop spans that retry would have saved.

## Consequences

- **The receiver classifies every Postgres error before responding.**
  A small SQLSTATE dispatch table sits at the boundary between the
  storage layer (ADR-0005's Layer 1) and the OTLP response
  formatter. Adding a new SQLSTATE is a one-line PR.
- **`db_errors_total{kind}` is the operator's diagnostic.**
  `kind=connection` rising means Postgres is sick; `kind=integrity`
  rising means a sender is emitting malformed spans; `kind=other`
  rising means we have an unclassified SQLSTATE worth investigating.
- **The blocklist (§3.1) does not use this path.** Blocked spans
  return silent OTLP success and are counted separately
  (`spans_blocked_total{pattern}`). The blocklist is the receiver's
  policy, not a sender failure — see §3.1.
- **A `rejected_spans` response is observable upstream** in the
  OTel collector's own metrics (`exporter_send_failed_spans` or
  similar, depending on the collector version). Operators can see
  drops without reading our metrics.
