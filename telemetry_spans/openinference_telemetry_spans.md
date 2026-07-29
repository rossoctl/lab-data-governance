# OpenInference Telemetry Spans Reference

This document covers the agentic instrumentation packages in
[Arize-ai/openinference](https://github.com/Arize-ai/openinference) as of main branch (June 2026).

## Tracer / Package Overview

| Package | PyPI name | Version | Tracer name (Python module path) |
|---|---|---|---|
| OpenAI Agents | `openinference-instrumentation-openai-agents` | 1.5.1 | `openinference.instrumentation.openai_agents` |
| Claude Agent SDK | `openinference-instrumentation-claude-agent-sdk` | 0.1.5 | `openinference.instrumentation.claude_agent_sdk` |
| Google ADK | `openinference-instrumentation-google-adk` | 0.1.15 | `openinference.instrumentation.google_adk` |
| Strands Agents | `openinference-instrumentation-strands-agents` | 0.1.2 | `openinference.instrumentation.strands_agents` |
| MCP | `openinference-instrumentation-mcp` | 2.0.3 | `openinference.instrumentation.mcp` |

**Kill-switch env var:** `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` (standard OTel mechanism) or
pass `exclusive_processor=False` to `OpenAIAgentsInstrumentor._instrument()` to add as a secondary
processor alongside the SDK's own tracing pipeline.

**Span-kind attribute:** Every span carries `openinference.span.kind` whose value is one of
`AGENT`, `CHAIN`, `LLM`, `TOOL`, `GUARDRAIL` (from `OpenInferenceSpanKindValues`).

**Span naming conventions:**

- **OpenAI Agents** — span name equals the `name` field of the underlying SDK `SpanData` object
  (e.g. agent name, function name, model name). Handoffs are named `"handoff to {target_agent}"`.
  Trace root spans use `trace.name`.
- **Claude Agent SDK** — hardcoded names: `ClaudeAgentSDK.query`,
  `ClaudeAgentSDK.ClaudeSDKClient.receive_response`, `ClaudeAgentSDK.{tool_name}` (subagent), or
  `ClaudeAgentSDK.Subagent` (when tool name is unknown). Tool spans use the raw tool name.
- **Google ADK** — `"invocation [{app_name}]"`, `"agent_run [{agent.name}]"`, plus spans named
  directly from the traced framework method.
- **Strands Agents** — native GenAI span names (`chat`, `execute_tool {name}`,
  `execute_event_loop_cycle`, `invoke_agent`) are re-labelled by the
  `StrandsAgentsToOpenInferenceProcessor` span processor.

---

## Section 1 — OpenAI Agents: Trace Root & Agent Spans

Source: `python/instrumentation/openinference-instrumentation-openai-agents/src/openinference/instrumentation/openai_agents/_processor.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{trace.name}` (e.g. `"My Agent"`) | AGENT | `openinference.span.kind` | `_processor.py` → `on_trace_start` | Root span created when an OpenAI Agents SDK `Trace` starts; represents the entire agent run. |
| `{AgentSpanData.name}` (the agent's configured name) | AGENT | `openinference.span.kind`, `graph.node.id`, `graph.node.parent_id` | `_processor.py` → `on_span_end` / `AgentSpanData` | One span per agent activation; `graph.node.id` is set to the agent name and `graph.node.parent_id` is set from the in-flight handoff dict to express the handoff chain. |

---

## Section 2 — OpenAI Agents: LLM Spans (Response & Generation)

Source: `_processor.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{ResponseSpanData.name}` or type fallback (`"response"`) | LLM | `openinference.span.kind`, `llm.system` (`openai`), `input.value`, `input.mime_type`, `output.value`, `output.mime_type`, `llm.model_name`, `llm.invocation_parameters`, `llm.input_messages.*`, `llm.output_messages.*`, `llm.tools.*`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`, `llm.token_count.prompt_details.cache_read`, `llm.token_count.completion_details.reasoning` | `_processor.py` → `ResponseSpanData` | An OpenAI Responses API call; captures the full request/response payload including messages, tool schemas, and token usage. |
| `{GenerationSpanData.name}` or type fallback (`"generation"`) | LLM | `openinference.span.kind`, `llm.system` (`openai`), `llm.model_name`, `llm.invocation_parameters`, `llm.provider`, `llm.input_messages.*`, `llm.output_messages.*`, `llm.token_count.prompt`, `llm.token_count.completion` | `_processor.py` → `GenerationSpanData` | A chat-completions-style LLM call inside the agent loop; captures model config, messages, and usage. |

---

## Section 3 — OpenAI Agents: Tool & Function Spans

Source: `_processor.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{FunctionSpanData.name}` (the function/tool name) | TOOL | `openinference.span.kind`, `llm.system` (`openai`), `tool.name`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type` | `_processor.py` → `FunctionSpanData` | Execution of a Python function registered as a tool; captures JSON-serialised input arguments and the return value. |
| `mcp_list_tools` (type fallback) | CHAIN | `openinference.span.kind`, `llm.system` (`openai`), `output.value`, `output.mime_type` | `_processor.py` → `MCPListToolsSpanData` | An MCP list-tools discovery call; result is JSON-serialised into `output.value`. |

---

## Section 4 — OpenAI Agents: Handoff Spans

Source: `_processor.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `"handoff to {HandoffSpanData.to_agent}"` | TOOL | `openinference.span.kind`, `llm.system` (`openai`) | `_processor.py` → `HandoffSpanData` | Represents a transfer of control from one agent to another; the to-agent name is embedded in the span name and cross-referenced to set `graph.node.parent_id` on the receiving agent's span. |

---

## Section 5 — OpenAI Agents: Guardrail & Custom Spans

Source: `_processor.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{GuardrailSpanData.name}` or type fallback | GUARDRAIL | `openinference.span.kind`, `llm.system` (`openai`) | `_processor.py` → `GuardrailSpanData` | A guardrail evaluation (input or output safety check) within the agent loop. |
| `{CustomSpanData.name}` or type fallback | CHAIN | `openinference.span.kind`, `llm.system` (`openai`) | `_processor.py` → `CustomSpanData` | A user-defined custom span emitted via the SDK's `custom_span()` context manager. |

---

## Section 6 — Claude Agent SDK: Agent Run Spans

Source: `python/instrumentation/openinference-instrumentation-claude-agent-sdk/src/openinference/instrumentation/claude_agent_sdk/_wrappers.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `ClaudeAgentSDK.query` | AGENT | `openinference.span.kind`, `llm.system` (`anthropic`), `input.value`, `input.mime_type`, `session_id`, `llm.model_name`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`, `llm.token_count.prompt_details.cache_read`, `llm.token_count.prompt_details.cache_write`, `llm.cost_total`, `llm.output_messages.*` | `_wrappers.py` → `_QueryWrapper` | Root span wrapping the `claude_agent_sdk.query()` async generator; covers the entire agentic conversation until the result message is received. |
| `ClaudeAgentSDK.ClaudeSDKClient.receive_response` | AGENT | `openinference.span.kind`, `llm.system` (`anthropic`), `input.value`, `input.mime_type`, `session_id`, `llm.model_name`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`, `llm.cost_total`, `llm.output_messages.*` | `_wrappers.py` → `_ClientReceiveResponseWrapper` | Per-turn agent span when using the stateful `ClaudeSDKClient`; covers one `receive_response()` call. |
| `ClaudeAgentSDK.{tool_name}` or `ClaudeAgentSDK.Subagent` | AGENT | `openinference.span.kind`, `agent.name` | `_wrappers.py` → `_SubagentSpanTracker` | Span for a sub-agent invocation launched via a tool call; `agent.name` carries the tool name when available. |

---

## Section 7 — Claude Agent SDK: Tool Spans

Source: `_wrappers.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{tool_name}` (raw tool name string) | TOOL | `openinference.span.kind`, `tool.id`, `tool.name`, `tool.parameters`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type` | `_wrappers.py` → `_ToolSpanTracker.start_tool_span` | One span per tool invocation inside a Claude agent run; keyed by `tool_use_id` and ended by `PreToolUse`/`PostToolUse` hooks. |

---

## Section 8 — Google ADK: Invocation & Agent Spans

Source: `python/instrumentation/openinference-instrumentation-google-adk/src/openinference/instrumentation/google_adk/_wrappers.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `"invocation [{app_name}]"` | CHAIN | `openinference.span.kind`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type`, `user_id`, `session_id` | `_wrappers.py` → `_RunnerRunAsync` | Top-level span for a single ADK `Runner.run_async()` invocation; captures the full input content and the final response event as JSON. |
| `"agent_run [{agent.name}]"` | AGENT | `openinference.span.kind`, `agent.name`, `output.value`, `output.mime_type` | `_wrappers.py` → `_BaseAgentRunAsync` | One span per `BaseAgent.run_async()` call in a multi-agent ADK pipeline; records the final response event as JSON output. |

---

## Section 9 — Google ADK: LLM Spans

Source: `_wrappers.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| _(name set by ADK's own tracing hook; span is augmented in-place)_ | LLM | `openinference.span.kind`, `llm.provider` (`google`), `llm.model_name`, `llm.invocation_parameters`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type`, `llm.input_messages.*` (role, content, tool calls, images), `llm.output_messages.*`, `llm.tools.*`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`, `llm.token_count.prompt_details.audio`, `llm.token_count.completion_details.audio`, `llm.token_count.completion_details.reasoning` | `_wrappers.py` → `_TraceCallLlm` | Augments an ADK-created LLM span with full OpenInference attributes; covers the request sent to Gemini (or another model) and the resulting response. |

---

## Section 10 — Google ADK: Tool Spans

Source: `_wrappers.py`

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| _(name set by ADK's own tracing hook; span is augmented in-place)_ | TOOL | `openinference.span.kind`, `tool.name`, `tool.description`, `tool.parameters`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type` | `_wrappers.py` → `_TraceToolCall` | Augments an ADK-created tool span with OpenInference attributes; records the tool's input parameters and the function response. |

---

## Section 11 — Strands Agents: Span Processor Transformations

The `StrandsAgentsToOpenInferenceProcessor` is a `SpanProcessor` that **rewrites** spans emitted by
the Strands Agents SDK (which uses GenAI semantic conventions) into OpenInference format. Span names
are preserved from the SDK; the processor maps them to OpenInference span kinds.

Source: `python/instrumentation/openinference-instrumentation-strands-agents/src/openinference/instrumentation/strands_agents/processor.py`

| Span name (native Strands name) | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `invoke_agent` (or span with `gen_ai.agent.name` / `agent.name`) | AGENT | `openinference.span.kind`, `llm.system` (`strands-agents`), `llm.provider` (`strands-agents`), `llm.model_name`, `llm.input_messages.*`, `llm.output_messages.*`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type`, `llm.token_count.*`, `graph.node.id` (`strands_agent`) | `processor.py` → `_determine_span_kind` / `invoke_agent` rule | Top-level agent run span; `graph.node.id` is always set to `"strands_agent"` for UI graph rendering. |
| `execute_event_loop_cycle` (or legacy `Cycle {uuid}`) | CHAIN | `openinference.span.kind`, `graph.node.id` (`cycle_{id}`), `graph.node.parent_id` (`strands_agent`), `llm.input_messages.*`, `llm.output_messages.*`, `input.value`, `output.value`, `llm.token_count.*` | `processor.py` → `execute_event_loop_cycle` rule | One iteration of the Strands agentic loop (reason → plan → optionally invoke tool); child of the agent span. |
| `chat` (or span with `"Model invoke"` in name) | LLM | `openinference.span.kind`, `llm.model_name`, `llm.invocation_parameters`, `llm.input_messages.*`, `llm.output_messages.*`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`, `graph.node.id` (`llm_{span_id}`) | `processor.py` → `chat` rule | A single LLM call (Bedrock Converse or compatible endpoint) within a Strands cycle; captures messages and token usage. |
| `execute_tool {tool_name}` (or legacy `Tool: {name}`) | TOOL | `openinference.span.kind`, `tool.name`, `tool.parameters`, `input.value`, `input.mime_type`, `output.value`, `output.mime_type`, `tool.call_id`, `tool.status`, `graph.node.id` (`tool_{name}_{span_id}`) | `processor.py` → `execute_tool` rule | Execution of one Strands tool; input parameters come from the `gen_ai.content.prompt` event and output from the `gen_ai.choice` event. |

---

## Section 12 — MCP: Transport Context Propagation

The `MCPInstrumentor` does **not** create new application-level spans. Instead it wraps the
low-level transport streams to inject and extract W3C `traceparent`/`tracestate` headers into the
MCP JSON-RPC `_meta` field, enabling distributed trace context to flow between MCP clients and
servers across all supported transports.

Source: `python/instrumentation/openinference-instrumentation-mcp/src/openinference/instrumentation/mcp/__init__.py`

| Instrumented point | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `streamable_http_client` (Streamable HTTP client) | _(context propagation only — no new span)_ | W3C `traceparent` / `tracestate` injected into `request.params._meta` | `__init__.py` → `InstrumentedStreamWriter.send` | Injects trace context into outgoing JSON-RPC requests over Streamable HTTP. |
| `StreamableHTTPServerTransport.connect` | _(context propagation only)_ | W3C `traceparent` extracted from `request.params._meta` | `__init__.py` → `InstrumentedStreamReader.__aiter__` | Extracts and attaches incoming trace context on the server side of Streamable HTTP. |
| `sse_client` (SSE client) | _(context propagation only)_ | W3C `traceparent` injected | `__init__.py` | Injects trace context into outgoing requests over SSE transport. |
| `SseServerTransport.connect_sse` | _(context propagation only)_ | W3C `traceparent` extracted | `__init__.py` | Extracts trace context on the server side of SSE transport. |
| `stdio_client` | _(context propagation only)_ | W3C `traceparent` injected | `__init__.py` | Injects trace context into stdio transport messages. |
| `stdio_server` | _(context propagation only)_ | W3C `traceparent` extracted | `__init__.py` | Extracts trace context from stdio transport messages. |
| `ServerSession.__init__` | _(context propagation only)_ | context saved on `_incoming_message_stream_writer`, restored in reader | `__init__.py` → `ContextSavingStreamWriter` / `ContextAttachingStreamReader` | Preserves OTel context across the MCP SDK's internal message hand-off stream so that server-side span parents are correctly linked. |

---

## Summary Count

| Package | AGENT spans | LLM spans | TOOL spans | CHAIN spans | GUARDRAIL spans | Context-propagation points |
|---|---|---|---|---|---|---|
| OpenAI Agents (`openai-agents`) | 2 (trace root, agent) | 2 (response, generation) | 2 (function, handoff) | 1 (custom) | 1 (guardrail) | — |
| Claude Agent SDK (`claude-agent-sdk`) | 3 (query, receive\_response, subagent) | — | 1 (tool) | — | — | — |
| Google ADK (`google-adk`) | 1 (agent\_run) | 1 (LLM augmentation) | 1 (tool augmentation) | 1 (invocation) | — | — |
| Strands Agents (`strands-agents`) | 1 (invoke\_agent) | 1 (chat) | 1 (execute\_tool) | 1 (execute\_event\_loop\_cycle) | — | — |
| MCP (`mcp`) | — | — | — | — | — | 7 transport wrap points |
| **Total distinct span types** | **7** | **3** | **4** | **3** | **1** | **7** |

**Grand total: 18 span types + 7 context-propagation instrumentation points across 5 packages.**

---

## Send / Receive Classification per Framework

> **Receive** — the agent is being called (inbound boundary).
> **Send** — the agent is calling out (outbound boundary).
> **Internal** — processing within a single entity; no protocol boundary.
> **Not observed** — the framework does not emit a span for this side.

### OpenAI Agents (`openinference-instrumentation-openai-agents`)

| Span name | Role | remote_label | Notes |
|---|---|---|---|
| `{trace.name}` (AGENT root) | Internal | — | Covers the entire run; not a protocol boundary |
| `{AgentSpanData.name}` (AGENT) | Internal | — | Within-process agent activation |
| `ResponseSpanData` / `GenerationSpanData` (LLM) | **Send** | `llm:<model>` from `llm.model_name` | Agent calling an LLM |
| `FunctionSpanData` (TOOL) | **Send** | `tool:<span.name>` | Agent invoking a local function tool |
| `"handoff to {target}"` (TOOL) | **Send** | `agent:<target>` from span name | Agent handing off to another agent; `target` is embedded in span name |
| `mcp_list_tools` (CHAIN) | **Send** | `mcp-server` (stub) | MCP tool-discovery call; no address attribute available |
| `GuardrailSpanData` (GUARDRAIL) | Internal | — | In-process safety check |
| `CustomSpanData` (CHAIN) | Internal | — | User-defined span; no protocol boundary |

### Claude Agent SDK (`openinference-instrumentation-claude-agent-sdk`)

| Span name | Role | remote_label | Notes |
|---|---|---|---|
| `ClaudeAgentSDK.query` (AGENT) | **Send** | `llm:claude` (from `llm.model_name`) | Root query to Claude; agent calling the Claude API |
| `ClaudeAgentSDK.ClaudeSDKClient.receive_response` (AGENT) | **Send** | `llm:claude` (from `llm.model_name`) | Per-turn stateful call to Claude |
| `ClaudeAgentSDK.{tool_name}` / `ClaudeAgentSDK.Subagent` (AGENT) | **Send** | `agent:<tool_name>` from `agent.name` | Sub-agent invocation launched via a tool call |
| `{tool_name}` (TOOL) | **Send** | `tool:<tool_name>` from `tool.name` | Local tool invocation |

### Google ADK (`openinference-instrumentation-google-adk`)

| Span name | Role | remote_label | Notes |
|---|---|---|---|
| `"invocation [{app_name}]"` (CHAIN) | Internal | — | Top-level runner; not itself a protocol boundary |
| `"agent_run [{agent.name}]"` (AGENT) | Internal | — | Within-pipeline agent activation |
| _(ADK LLM span, augmented)_ (LLM) | **Send** | `llm:<model>` from `llm.model_name` | Agent calling Gemini or compatible model |
| _(ADK tool span, augmented)_ (TOOL) | **Send** | `tool:<name>` from `tool.name` | Agent invoking a tool function |

### Strands Agents (`openinference-instrumentation-strands-agents`)

| Span name | Role | remote_label | Notes |
|---|---|---|---|
| `invoke_agent` (AGENT) | Internal | — | Top-level agent span; not a protocol boundary |
| `execute_event_loop_cycle` (CHAIN) | Internal | — | One reasoning iteration; internal structure |
| `chat` (LLM) | **Send** | `llm:<model>` from `llm.model_name` | Agent calling Bedrock Converse (or compatible) |
| `execute_tool {tool_name}` (TOOL) | **Send** | `tool:<tool_name>` from `tool.name` | Agent invoking a Strands tool |

### MCP (`openinference-instrumentation-mcp`)

No application spans emitted. Context propagation only — the HTTP SERVER span from `opentelemetry-instrumentation-starlette` provides the **Receive** boundary on the MCP server side; the HTTP CLIENT span from `opentelemetry-instrumentation-httpx` provides the **Send** boundary on the client side.

---

## Receive gaps

None of the openinference packages emit a **Receive** span. Inbound boundaries are always covered by the HTTP layer (`starlette` SERVER span). If an agent framework is deployed without HTTP instrumentation, the receive side will be missing and the trace graph will be split at that boundary.
