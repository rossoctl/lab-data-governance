# The verdict service

`GET /enforce/v1/verdicts/{trace_id}/{exchange_id}?wait_ms=N` (`data_governance/risk/api/verdict_routes.py`,
Deployment `deploy/k8s/45-verdict.yaml`) answers a Sentry gate holding one outbound request: the verdict of the
exchange's **Interaction risk record**, read, never written (leg-ready is its only writer). `wait_ms` 0..3000 makes it
a long poll; a larger value is refused, never clamped.

The verdict is decided on what the record's rules saw of the request payload; `RISK_INHERITED_LABELS` is `off` in the
shipped manifests, so that is the payload's own labels.

## The answer (HTTP 200)

| field | when | what |
|---|---|---|
| `state` | always | `decided`, `unjudged` or `pending` (below) |
| `exchange_id`, `waited_ms` | always | the exchange asked about; the time this request waited |
| `verdict`, `version` | decided, unjudged | the record's enforcement type as the policy wrote it (a gate denies only those in its `deny_on`, by default `block`), and its version |
| `reason` | unjudged | `body_not_captured` or `body_truncated` |

- **`decided`:** a record evidences the request leg, and the sidecar reported the body whole or there was none. With no
  request leg at all (a derivation that never writes one), the latest record.
- **`unjudged`:** such a record exists, but its rules did not see the whole request body, as the exchange's request span
  says (wire contract §5). A size above 0 beside no `input.value` is `body_not_captured` (`capture_io` off, a body that
  is not text or not whole, a parser that reduced it to nothing); so is neither a value nor a size, from a sidecar older
  than contract v1.8. A value ending `…[truncated]` is `body_truncated`. The answer comes at once, with no wasted wait.
- **`pending`:** `wait_ms` passed with no record.

The gate refuses an `unjudged` answer unless its verdict is one it denies anyway. So **with `capture_io` off every gated
request that has a body is refused**, MCP `tools/list` and `initialize` to a gated outside host included. With it on, so
is a body longer than `max_payload_bytes` (default 4096) unless what was read blocks it, and an MCP `ping`,
`notifications/initialized` or `tools/list` with no params, which the parser reduces to nothing, so an MCP session to a
gated outside host does not get through its handshake. A request with no body is judged either way. No setting changes
any of this yet.

**What `decided` does not cover.** For a2a, mcp and inference, `input.value` is the parser's reduction of the body, not
the body. What a parser leaves out of it no record judges, and no span fact says it was left out, so the service cannot
tell. Plain HTTP carries the whole text body.

Errors: 400 (`bad_key`, `bad_wait_ms`, `wait_ms_too_large`), 401 `unauthorized`, 503 `verdict_busy` (the per-process
waiter cap, never a queue), 500 `verdict_unavailable` (the database did not answer, within the manifest's
`DB_POOL_TIMEOUT` of 3 s). The gate treats anything else as no verdict.

## Who may ask

`Authorization: Bearer <VERDICT_SECRET>` (constant-time compare). The process refuses to start without the secret;
`dg.sh component install` creates Secret `dg-verdict-secret` when absent, and the manual steps create it by hand. The
NetworkPolicy admits namespaces labelled `rossoctl-enabled=true` to this port; the full API stays platform-only. The
secret identifies a gated sidecar, not a workload (issue #343).

A gate reads the value from its own config or a mounted file (`verdict_secret`, `verdict_secret_file`), so it has to be
copied into each gated namespace; nothing here does that. `dg.sh component uninstall` without `--keep-data` deletes the
Secret and the next install draws a new value, after which gates holding the old one get 401 and refuse.

## How the wait is short

Three things keep the answer inside the time the gate holds the request:

- **One read per wake.** Each process keeps one waiter map and one dispatcher on the change hub (`dg_legs_inserted`,
  `dg_interaction_risk_written`). Every wake, and at least every 200 ms while anyone waits, it runs one read for all
  waited interactions.
- **Priority classification** (`VERDICT_PRIORITY_CLASSIFICATION`). A waited request payload not classified yet is
  classified in this process, through the classifier's own write-once `process_payload`, racing the cursor loop
  harmlessly. That is why the service runs from the classification image, and why it reports ready only after the model
  has classified a fixed sample. It keeps no queue: a payload that finds every worker busy is offered again on the next
  pass, while someone still waits on it.
- **Priority decision** (`VERDICT_PRIORITY_DECISION` here, `LEG_READY_PRIORITY_DECISION` on leg-ready). The service names
  the waited interaction on `dg_priority_decision`, and leg-ready computes it as soon as its request leg is ready, out
  of `seq` order, in a turn shared with the cursor arm. A lost notify costs only latency; the cursor arm still reaches
  the leg.

The sidecar must export its request span well inside `wait_ms`, which is lineage's `export_delay_ms`: the service can
answer only once it has the span, and that setting's default of 5000 ms is past any wait. A gate's `verdict_url` ends in
`/enforce/v1`.

Classification cost grows with the body. Measured with the real model at a 2-CPU limit: 4 KB 1.2 s, 16 KB 3.9 s,
64 KB 16.7 s, 256 KB 70 s, and 512 KB killed the pod at its 3 GiB limit. So at the default `max_payload_bytes` one payload
fits a gate's wait, a burst of full-size payloads beyond the wait's worth of classifications does not (those requests
answer `pending` and are refused), and a raised cap or `-1` means bodies above about 10 KB cannot be decided in time and a very large
one can take the service down: raise it on gated sidecars only with that in mind. `VERDICT_READ_WORKERS` (4) plus
`VERDICT_PRIORITY_CLASSIFICATION_CONCURRENCY` (1) plus 1 is the pool the manifest sets in `DB_POOL_MAX_SIZE`; change
them together.

## What it does not do

It never calls OPA and never writes a risk record. A workload's call to the full API is still refused, and this service
serves the verdict, `/healthz` and `/readyz` only.
