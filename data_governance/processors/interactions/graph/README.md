# P-interactions graph algorithm

The graph-based P-interactions algorithm (ADR-0026): a batch derivation of
**entities** and **interactions** from a trace's spans, answering **does this
produce a sensible execution flow on real Kagenti agent traces?** — validated
against the captured fixtures in `tests/processors/interactions/graph/`.

It is now one of the two production algorithms (the other being the streaming
`processors/interactions/`), selected by `INTERACTIONS_ALGORITHM=graph`. The pure
core is `extractor.extract(spans)`; `interactions/graph_adapter.py` maps its output
onto the production schema and `interactions/graph_driver.py` drives it into the
real tables via `state.flush`.

The package lives at `data_governance/processors/interactions/graph/` (a
sub-package of the production `interactions/` package, alongside the streaming
algorithm and the shared identity code) and keeps its per-step module layout
(`step1_build_graph`, `step2_base_graph`, `step3_entity_graph`, wired by
`builder`). The `cli.py` tool is dev/debug only — it materialises the intermediate
graph tables for eyeballing the coloring/inference, and does NOT write the final
tables (the production driver does).

## Assumptions baked in (revisit after seeing output)

- **Anchor for cross-service hop:** outermost SERVER span on the callee side
  whose parent span belongs to a different `service_name` (or is missing /
  orphan).
- **Anchor for LLM call (uninstrumented callee):** the OpenInference
  `generation` INTERNAL span. Its `CLIENT POST` child is evidence.
- **Anchor for local tool call (in-process tool):** the OpenInference
  TOOL-kind INTERNAL span (e.g. `search_destinations`).
- **Entity kinds:**
  - `agent` — `service_name` whose spans contain `openinference.span.kind`
    in {`AGENT`, `CHAIN`}.
  - `tool` — `service_name` whose SERVER spans expose an `/mcp` HTTP path.
    Or, for local tools, identified by tool-span `name` and parent service.
  - `external_client` — service that emits only CLIENT/INTERNAL spans
    in this trace and originates the trace's first cross-service hop.
  - `external_service` — referenced via CLIENT POST `attributes['http.url']`
    host, no SERVER span anywhere with that service.
  - `user` — not detected by this algorithm (no UI annotation present).
- **Identity (standalone/debug only):** UUIDs for the graph's own `entities.id` and
  `interactions.id`. In production these are re-derived deterministically by
  `interactions/graph_adapter.py` (uuid5 of the natural key / anchor) — see
  ADR-0026 and the adapter.
- **Payload extraction:**
  - `llm.input_messages.*` → `llm_chat_prompt`
  - `llm.output_messages.*` → `llm_completion`
  - tool input attributes (`input.value`) → `tool_call_arguments`
  - tool output attributes (`output.value`) → `tool_call_result`
  - HTTP request/response bodies → not extracted by this algorithm (rare on these traces)
  - everything else recognised as payload-shaped → `unknown`
- **Canonicalization:** sorted-keys JSON for chat prompts and completions;
  raw JSON for tool args/results; raw bytes for `unknown`. SHA-256 over
  the canonical bytes.

## Run

**Production** (derive into the real `entities` / `interactions` tables):

```bash
INTERACTIONS_ALGORITHM=graph DATABASE_URL="postgres://..." \
  python -m data_governance.processors.interactions
```

**Debug** (materialise the intermediate graph tables for one trace, to eyeball the
coloring/inference):

```bash
DATABASE_URL="postgres://..." \
  python -m data_governance.processors.interactions.graph.cli <trace_id>
```

The debug CLI drops + recreates the intermediate `proto_base_*` / `proto_colored_*`
/ `proto_entity_*` tables on each run and writes only those — the final entities /
interactions live in the production tables, written by the driver above.
