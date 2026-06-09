# Graph-builder test fixtures

`real_trace.json` — one real trace (`fc9282b2a10ffb023b0fc4bdd69782ce`, 73 spans)
captured from the live `data-governance` Postgres on the `kind-kagenti` cluster
(agent-examples-snp payment-agent flow). Only the columns the graph-builder
reads are kept (`trace_id, span_id, parent_id, name, kind, service_name,
started_at, attributes`). Used by `test_backfill_real_data.py` to validate the
`semantic_kind` ladder and edge derivation against genuine post-collector-
transform attribute keys — not synthetic data.
