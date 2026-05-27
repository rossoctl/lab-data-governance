# P-interactions prototype (THROWAWAY)

Throwaway logic prototype answering: **does the proposed P-interactions
algorithm produce a sensible execution flow when run on real Kagenti agent
traces?**

The grilling session in CONTEXT.md / docs/PROJECT.md left several questions
open. This prototype picks the most defensible answer for each, runs the
algorithm against real spans, and exposes the output through a small UI tab
so the result can be eyeballed.

The contents of this directory will be deleted once the algorithm has been
either validated (folded into a real `data_governance/processors/p_interactions/`
module) or rejected (back to the grilling table).

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
  - `user` — not detected by this prototype (no UI annotation present).
- **Identity:** UUIDs for `entities.id` and `interactions.id`. OTEL anchor
  carried only via `interaction_spans`.
- **Payload extraction:**
  - `llm.input_messages.*` → `llm_chat_prompt`
  - `llm.output_messages.*` → `llm_completion`
  - tool input attributes (`input.value`) → `tool_call_arguments`
  - tool output attributes (`output.value`) → `tool_call_result`
  - HTTP request/response bodies → not extracted in prototype (rare on these traces)
  - everything else recognised as payload-shaped → `unknown`
- **Canonicalization:** sorted-keys JSON for chat prompts and completions;
  raw JSON for tool args/results; raw bytes for `unknown`. SHA-256 over
  the canonical bytes.

## Run

```bash
DATABASE_URL="postgres://..." \
  python -m data_governance.processors.p_interactions_proto.cli \
    05c6095d1f863dcb3b209ef4761829e1
```

Writes to scratch tables `proto_entities`, `proto_interactions`,
`proto_interaction_spans`, `proto_interaction_payloads`. The scratch
tables are dropped and recreated on each run.
