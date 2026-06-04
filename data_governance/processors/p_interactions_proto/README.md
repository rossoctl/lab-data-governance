# P-interactions prototype (THROWAWAY)

Throwaway logic prototype answering: **does the
`P-interactions` algorithm in CONTEXT.md + ADRs 0007–0010 produce a
sensible execution flow when run on real Kagenti agent traces?**

Round 3 of the prototype was rewritten end-to-end to match the design
captured in:

- [`CONTEXT.md`](../../../CONTEXT.md) — `Entity`, `Interaction`,
  `Natural key`, `Anchor span`, `Interaction tree`, `Provisional entity`,
  `P-interactions`, `Interaction-span role`, `Entity-span role`.
- [`docs/adr/0007-p-interactions-streaming-model.md`](../../../docs/adr/0007-p-interactions-streaming-model.md)
  — streaming, eventually consistent, no completion flag.
- [`docs/adr/0008-interaction-forest-no-synthetic-root.md`](../../../docs/adr/0008-interaction-forest-no-synthetic-root.md)
  — interaction forest per trace; trace root span is not an
  interaction.
- [`docs/adr/0009-anchor-rules-entity-kind-agnostic.md`](../../../docs/adr/0009-anchor-rules-entity-kind-agnostic.md)
  — five structural anchor rules + separate caller-inference ladder.
- [`docs/adr/0010-mcp-tools-emit-both-interactions.md`](../../../docs/adr/0010-mcp-tools-emit-both-interactions.md)
  — agent → tool (in-process) and tool → tool (cross-service) both
  emit; tree links them.

The directory is throwaway: when the design has been validated, fold
the per-span procedure into a real
`data_governance/processors/p_interactions/` module and delete this
prototype.

## Module layout

| File | Purpose |
|---|---|
| `caller_inference.py` | Pure entity-kind ladder. Span attributes → `(kind, natural_key, display_name)`. |
| `anchor_rules.py` | Five structural anchor rules per ADR-0009. Pure functions of span structure. |
| `procedure.py` | Per-span 9-step `Processor`. Stateful in-memory state machine; idempotent on re-process. |
| `cli.py` | Driver: fetches a trace's spans, runs `extract`, prints a forest report, writes `proto_*` scratch tables. |
| `extractor.py` | Compatibility shim re-exporting from `procedure`. |
| `NOTES.md` | Round 1 / 2 / 3 history + verdicts. |

## Schema

The CLI's DDL drops + recreates these scratch tables on each
run (mirroring Q21 / ADR-0007 with ENUMs stored as `text` to avoid
alembic friction):

- `entities`
- `entity_spans` (role: `discovered_via` | `identified_via`)
- `interactions` (`parent_interaction_id`, `seq`, `anchor_rule`
  debug column)
- `interaction_spans` (role: `anchor` | `info` | `connector`)
- `interaction_payloads` (content-addressed)
- `processor_state` (cursor: `last_processed_seq`)

## Run

The receiver image already has `data_governance` and a working
`DATABASE_URL` baked in, so the easiest way to run the prototype is
inside the receiver pod. Copy the prototype directory in (the
deployed image doesn't ship it), then exec the CLI.

```bash
POD=$(kubectl --context kind-kagenti -n data-governance \
  get pod -l app=data-governance-receiver -o name | head -1 | sed 's|pod/||')

# 1. Copy the prototype into the pod
kubectl --context kind-kagenti -n data-governance cp \
  data_governance/processors/p_interactions_proto \
  $POD:/app/data_governance/processors/p_interactions_proto

# 2. Run the CLI against a trace_id
kubectl --context kind-kagenti -n data-governance exec $POD -- \
  python -m data_governance.processors.p_interactions_proto.cli \
  05c6095d1f863dcb3b209ef4761829e1
```

Re-copy the directory after every edit; pods don't auto-sync.

### Late-parent torture test

Add `--scramble` to reverse span-arrival order — every child arrives
before its parent. Exercises the deferred-spans queue and late-parent
re-evaluation paths.

```bash
kubectl --context kind-kagenti -n data-governance exec $POD -- \
  python -m data_governance.processors.p_interactions_proto.cli \
  05c6095d1f863dcb3b209ef4761829e1 --scramble
```

### Inspecting the scratch tables

The CLI's stdout report is the fast feedback loop. For deeper
inspection (e.g., per-span attachment), query the `proto_*` tables
directly via `psql` or the `db` module. Example:

```python
from data_governance import db
db.configure(os.environ["DATABASE_URL"])
with db.transaction() as txn:
    print(txn.fetch_all(
        "SELECT kind, count(*) FROM entities GROUP BY kind ORDER BY 1"
    ))
```

## Local-only run (without a pod)

The prototype is a thin layer over `data_governance.retrieval` and
`data_governance.db`, both of which require Postgres. If you have a
DSN that reaches the receiver's database (e.g. via
`kubectl port-forward svc/data-governance-postgres 5432:5432`), the
CLI runs locally too:

```bash
kubectl --context kind-kagenti -n data-governance \
  port-forward svc/data-governance-postgres 5432:5432 &

DATABASE_URL='postgresql://...your-creds...' \
  uv run python -m data_governance.processors.p_interactions_proto.cli \
    05c6095d1f863dcb3b209ef4761829e1
```

The pod-internal route is faster for iteration (no port-forward, no
local creds) and is what round-3 development used.

## Test trace

Round 3 is validated against
`05c6095d1f863dcb3b209ef4761829e1` (498 spans, 7 services: a
demo-client, three agents, three deployed MCP tools, and an
ete-litellm LLM gateway). See [`NOTES.md`](NOTES.md) §"Round 3 verdicts"
for the expected output and the handful of known warts (round-2
Problem 2 is still deferred).
