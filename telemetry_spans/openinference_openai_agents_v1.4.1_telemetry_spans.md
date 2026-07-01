# OpenInference OpenAI Agents — Telemetry Spans Reference

| Field | Value |
|---|---|
| Package | [`openinference-instrumentation-openai-agents`](https://github.com/Arize-ai/openinference/tree/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents) |
| Tag | `python-openinference-instrumentation-openai-agents-v1.4.1` |
| Resolved commit | `c1447128de5859e999d6adaf02df7189363baa01` |
| Tracer name (Python module path) | `openinference.instrumentation.openai_agents` |
| Tracer version | `1.4.1` (verified against [`version.py`](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/version.py)) |
| Underlying SDK | `openai-agents >= 0.2.6` |

**Kill-switch:** there is no env var. Disable by either:

- **Standard OTel mechanism** — set `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=openinference.instrumentation.openai_agents` in your environment, or
- **Bypass `set_trace_processors`** — call `OpenAIAgentsInstrumentor()._instrument(exclusive_processor=False)` to add this tracer alongside the SDK's own pipeline instead of replacing it.

## Span-naming convention

The tracer uses the OpenAI Agents SDK's own `TracingProcessor` interface; one OTel span is opened per `agents.tracing.Span`, plus one root span per `agents.tracing.Trace`. Names come from [`_get_span_name`](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L212-L217):

1. If the underlying `SpanData` exposes a `.name` string, that is the span name.
2. Else if it is a `HandoffSpanData` with `to_agent` set → `"handoff to {to_agent}"`.
3. Else fall back to `span_data.type` (a literal type-name string defined by the agents SDK, e.g. `"response"`, `"generation"`, `"mcp_list_tools"`).

OTel `SpanKind` is the default **`INTERNAL`** for every span; `start_span` never overrides `kind=`. The semantic kind lives in the **`openinference.span.kind`** attribute (one of `AGENT` / `LLM` / `TOOL` / `CHAIN` / `GUARDRAIL`), set by [`_get_span_kind`](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L220-L235).

Every inner span carries `llm.system="openai"` regardless of subtype (set unconditionally on span start, [`_processor.py:128`](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L128)).

In the tables below, *Kind* is the OpenInference semantic kind (the `openinference.span.kind` attribute value); the OTel `SpanKind` is INTERNAL for every row.

---

## Section 1 — Trace Root Spans

Source: [`_processor.py` → `on_trace_start`](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L82-L94)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{trace.name}` (e.g. `"My Agent"`) | AGENT | `openinference.span.kind` | `_processor.py` → `on_trace_start` | Wraps a complete agent run; opened on `Trace` start, closed on `Trace` end. Note: `llm.system` is NOT set on the root span (it's only added in `on_span_start` for inner spans). |

---

## Section 2 — Agent Activation Spans

Source: [`_processor.py` → `on_span_end` / `AgentSpanData` branch](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L181-L186)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{AgentSpanData.name}` | AGENT | `openinference.span.kind`, `llm.system` (`openai`), `graph.node.id` (= `data.name`), `graph.node.parent_id` (set when this agent was reached via a handoff) | `_processor.py` → `AgentSpanData` | One span per agent activation. `graph.node.parent_id` is back-populated from a handoff dict the processor maintains across spans (`_reverse_handoffs_dict`, capped at 1000 in-flight entries). |

---

## Section 3 — LLM Spans (Responses & Generation)

Source: [`_processor.py` → `on_span_end` / `ResponseSpanData` and `GenerationSpanData` branches](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L148-L166)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{ResponseSpanData.name}` or fallback `"response"` | LLM | `openinference.span.kind`, `llm.system` (`openai`), `input.value`, `input.mime_type`, `output.value` (=`response.model_dump_json()`), `output.mime_type` (`application/json`), `llm.model_name`, `llm.invocation_parameters`, `llm.input_messages.{i}.*`, `llm.output_messages.{i}.*`, `llm.tools.{i}.tool.json_schema`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`, `llm.token_count.prompt_details.cache_read`, `llm.token_count.completion_details.reasoning` | `_processor.py` → `ResponseSpanData` | Span for an OpenAI Responses-API call. Captures the full request/response payload, system instructions injected as `llm.input_messages.0.{role,content}`, tool schemas, and token usage. |
| `{GenerationSpanData.name}` or fallback `"generation"` | LLM | `openinference.span.kind`, `llm.system` (`openai`), `llm.model_name`, `llm.invocation_parameters`, `llm.provider` (`openai`, set only when `model_config.base_url` contains `api.openai.com`), `input.value`, `input.mime_type`, `output.value`, `output.mime_type`, `llm.input_messages.{i}.*`, `llm.output_messages.{i}.*`, `llm.token_count.prompt`, `llm.token_count.completion` | `_processor.py` → `GenerationSpanData` | Span for a chat-completions-style LLM call. No total-token attribute here (only prompt/completion); no token-detail breakdowns. |

Within message-list attributes (both LLM span types):
- Per-message: `llm.input_messages.{i}.message.role`, `…content`, `…tool_call_id`, `…tool_calls.{j}.tool_call.id`, `…tool_calls.{j}.tool_call.function.name`, `…tool_calls.{j}.tool_call.function.arguments` (the latter omitted when arguments serialise to `"{}"`).
- Per-content-part: `…message.contents.{i}.message_content.type` and `…message_content.text` (image and audio parts are unimplemented TODO branches).

---

## Section 4 — Tool & Function Spans

Source: [`_processor.py` → `on_span_end` / `FunctionSpanData` and `MCPListToolsSpanData` branches](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L167-L172)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{FunctionSpanData.name}` (the function/tool name) | TOOL | `openinference.span.kind`, `llm.system` (`openai`), `tool.name` (= span name), `input.value` (raw JSON arguments), `input.mime_type` (`application/json`), `output.value`, `output.mime_type` (`application/json` if the output is a string starting with `{` and ending with `}`, otherwise unset) | `_processor.py` → `FunctionSpanData` | One span per Python-function tool invocation. The output is converted to a primitive type (`safe_json_dumps` for list/tuple/dict, `str` for everything else). |
| `mcp_list_tools` (type fallback) | CHAIN | `openinference.span.kind`, `llm.system` (`openai`), `output.value` (= `safe_json_dumps(data.result)`), `output.mime_type` (`application/json`) | `_processor.py` → `MCPListToolsSpanData` | An MCP tool-discovery call. `MCPListToolsSpanData` has no `.name` attribute, so the name falls back to the SpanData type literal `"mcp_list_tools"`. |

---

## Section 5 — Handoff Spans

Source: [`_processor.py` → `on_span_end` / `HandoffSpanData` branch](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L173-L180)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `"handoff to {HandoffSpanData.to_agent}"` | TOOL | `openinference.span.kind`, `llm.system` (`openai`) | `_processor.py` → `HandoffSpanData` | Represents control transfer between agents. The handoff itself sets only the kind and `llm.system`; the destination agent's name is solely encoded in the span name. The processor also records `(to_agent, trace_id) → from_agent` so the receiving agent's `AgentSpanData` span can read `graph.node.parent_id`. |

---

## Section 6 — Guardrail & Custom Spans

Source: [`_processor.py` → `on_span_start` (no per-data attribute branch in `on_span_end`)](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L106-L132) + [`_get_span_kind`](https://github.com/Arize-ai/openinference/blob/c1447128de5859e999d6adaf02df7189363baa01/python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py#L220-L235)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{GuardrailSpanData.name}` or type fallback (`"guardrail"`) | CHAIN | `openinference.span.kind`, `llm.system` (`openai`) | `_processor.py` → `GuardrailSpanData` | A guardrail evaluation. `_get_span_kind` returns CHAIN for guardrail spans in this version (despite OpenInference defining a separate GUARDRAIL kind). No payload attributes are extracted. |
| `{CustomSpanData.name}` or type fallback (`"custom"`) | CHAIN | `openinference.span.kind`, `llm.system` (`openai`) | `_processor.py` → `CustomSpanData` | A user-defined span emitted via the SDK's `custom_span()` context manager. No payload extraction. |

---

## Summary Count

| Section | Span kinds emitted | Span shapes |
|---|---|---|
| 1 — Trace root | AGENT | 1 (`{trace.name}`) |
| 2 — Agent activation | AGENT | 1 (`{AgentSpanData.name}`) |
| 3 — LLM | LLM | 2 (response, generation) |
| 4 — Tool & function | TOOL, CHAIN | 2 (function, mcp_list_tools) |
| 5 — Handoff | TOOL | 1 (`"handoff to {target}"`) |
| 6 — Guardrail & custom | CHAIN | 2 (guardrail, custom) |
| **Total** | — | **9 distinct span shapes** |

Distinct semantic kinds emitted: **AGENT** (×2 shapes), **LLM** (×2), **TOOL** (×2), **CHAIN** (×3). GUARDRAIL is defined by OpenInference but **not emitted** by this version — guardrail spans are tagged CHAIN (see Section 6).
