# OpenInference Anthropic — Telemetry Spans Reference

| Field | Value |
|---|---|
| Package | [`openinference-instrumentation-anthropic`](https://github.com/Arize-ai/openinference/tree/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic) |
| Tag | `python-openinference-instrumentation-anthropic-v1.0.6` (released 2026-06-02) |
| Resolved commit | `06c997945f003cadfad7d60e7e57061a8b156b50` |
| Tracer name (OTel scope name) | `openinference.instrumentation.anthropic` |
| Tracer / scope version | `1.0.6` (verified against [`version.py`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/version.py)) |
| Underlying SDK | `anthropic >= 0.84.0` (the raw Anthropic Python client — **not** the Claude Agent SDK) |

This package wraps the raw Anthropic Python client's `client.messages.create`, `client.messages.parse`, `client.messages.stream`, the legacy `client.completions.create`, and the `client.beta.messages.*` equivalents. It is distinct from `openinference-instrumentation-claude-agent-sdk` (which instruments the agentic Claude Agent SDK). Every span it emits is a single, self-contained **LLM** span representing one HTTP call to the Anthropic API.

**Kill-switch:** there is no dedicated env var. Disable via the standard OTel mechanism — set `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=openinference.instrumentation.anthropic` — or call `AnthropicInstrumentor().uninstrument()` to restore the original SDK methods. The wrappers also honour the OTel `_SUPPRESS_INSTRUMENTATION_KEY` context value (no span is created while suppression is active).

## Span-naming convention

Names are **hardcoded constants** passed to each wrapper at instrumentation time (`__init__.py`), not derived from any runtime value. There is one name per wrapped SDK method:

| Wrapped SDK method (sync & async) | Span name |
|---|---|
| `completions.create` | `completions.create` |
| `messages.create` | `messages.create` |
| `messages.parse` | `messages.parse` |
| `messages.stream` | `messages.stream` |
| `beta.messages.create` | `beta.messages.create` |
| `beta.messages.parse` | `beta.messages.parse` |
| `beta.messages.stream` | `beta.messages.stream` |

The sync and async variants of each method share the same span name (e.g. both `Messages.create` and `AsyncMessages.create` emit `messages.create`). The `beta.*` names are the only thing that distinguishes a beta-namespace call from the stable one.

**OTel `SpanKind`** is the default **`INTERNAL`** for every span — `start_span` is called without `kind=` ([`_wrappers.py:174`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_wrappers.py#L174)). The semantic kind lives in the **`openinference.span.kind`** attribute, which is **always `LLM`** for this instrumentation (`_get_llm_span_kind` unconditionally yields `LLM`; there are no AGENT / TOOL / CHAIN spans). In the tables below, *Kind* is the `openinference.span.kind` value.

Every span also unconditionally carries both `llm.provider = "anthropic"` and `llm.system = "anthropic"` (set on span start for all wrappers).

---

## Section 1 — Messages API spans (`messages.create` / `messages.parse`, non-streaming)

Source: [`_wrappers.py` → `_MessagesWrapper` / `_AsyncMessagesWrapper`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_wrappers.py#L318-L421)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `messages.create` / `messages.parse` (and `beta.messages.create` / `beta.messages.parse`) | LLM | **On start:** `openinference.span.kind` (`LLM`), `llm.provider` (`anthropic`), `llm.system` (`anthropic`), `llm.model_name` (from request `model`), `llm.invocation_parameters` (all kwargs minus `model`/`system`/`messages`/`tools`/`extra_headers`/`output_format`, JSON), `llm.input_messages.{i}.*`, `llm.tools.{j}.tool.json_schema`, `input.value` (full request kwargs, JSON), `input.mime_type` (`application/json`), plus any context attributes from `get_attributes_from_context()`. **On completion:** `llm.model_name` (overwritten from `response.model`), `llm.output_messages.{i}.*`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.prompt_details.cache_read`, `llm.token_count.prompt_details.cache_write`, `output.value` (= `response.model_dump_json()`), `output.mime_type` (`application/json`). | `_wrappers.py` → `_MessagesWrapper` | One span per non-streaming `messages.create` / `messages.parse` call. Captures the full request payload and the assistant response (text + tool-use blocks). If `stream=True` is passed to `create`, the wrapper instead returns a `_MessagesStream` proxy and the span is finished by the stream path (see Section 3). |

---

## Section 2 — Messages streaming-manager spans (`messages.stream`)

Source: [`_wrappers.py` → `_MessagesStreamWrapper` / `_AsyncMessagesStreamWrapper` + `_MessageStreamManager` proxies](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_wrappers.py#L424-L658) and [`_stream.py` → `_RawStreamInterceptor` / `_MessageExtractor`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_stream.py#L38-L100)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `messages.stream` (and `beta.messages.stream`) | LLM | **On start:** same start-attribute set as Section 1 (`openinference.span.kind`, `llm.provider`, `llm.system`, `llm.model_name` from request, `llm.invocation_parameters`, `llm.input_messages.*`, `llm.tools.*`, `input.value`, `input.mime_type`, context attributes). **On completion** (from the accumulated `current_message_snapshot` via `_MessageExtractor`): `output.value` (= snapshot `model_dump_json()`), `output.mime_type` (`application/json`), `llm.output_messages.0.message.role`, `llm.output_messages.0.message.content` (text), `llm.output_messages.0.message.tool_calls.{k}.tool_call.function.name` / `.arguments`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`. | `_wrappers.py` → `_MessagesStreamWrapper`; `_stream.py` → `_RawStreamInterceptor` | Span for the `messages.stream(...)` context-manager API. The wrapper returns an `ObjectProxy` over the SDK's `MessageStreamManager`; on `__enter__` it splices a `_RawStreamInterceptor` into the raw HTTP stream so the SDK still does its own `accumulate_event`, and finishes the span when the stream is exhausted (or errors), reading the final message from `current_message_snapshot`. Note: unlike Section 1, completion does **not** re-emit `llm.model_name` from the response, but **does** add `llm.token_count.total`; cache-token detail breakdowns are **not** emitted on this path. |

---

## Section 3 — Messages `create(stream=True)` spans

Source: [`_stream.py` → `_MessagesStream` / `_MessageResponseAccumulator` / `_MessageExtractor`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_stream.py#L212-L346)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `messages.create` (and `beta.messages.create`) — streaming variant | LLM | Same start attributes as Section 1. **On completion** (via `_MessageExtractor` over the SDK-accumulated snapshot): `output.value`, `output.mime_type` (`application/json`), `llm.output_messages.0.message.role`, `llm.output_messages.0.message.content`, `llm.output_messages.0.message.tool_calls.{k}.tool_call.function.name` / `.arguments`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`. | `_stream.py` → `_MessagesStream` | When `messages.create(..., stream=True)` is used, `_MessagesWrapper` returns a `_MessagesStream` proxy that re-accumulates raw SSE events with the SDK's `accumulate_event` and finishes the span when iteration completes or raises. Same span **name** as the non-streaming `messages.create` (Section 1) but a different completion-attribute shape (adds `total` token count; omits the cache-token detail breakdowns and the response-side `llm.model_name`). |

### Input-message attribute detail (Sections 1–3)

`_get_llm_input_messages` walks the request `messages` list (and the separate top-level `system` parameter). Per message at index `i`:

- `llm.input_messages.{i}.message.role`
- `llm.input_messages.{i}.message.content` — for a plain-string `content`, or for the stringified content of a `tool_result` block
- Per structured content block `j`: `llm.input_messages.{i}.message.contents.{j}.message_content.type` (`"text"` / `"image"`) with `…message_content.text` or `…message_content.image.image.url` (a `data:` URI assembled from the block's `source`)
- `tool_use` blocks → `llm.input_messages.{i}.message.tool_calls.{t}.tool_call.id`, `…tool_call.function.name`, `…tool_call.function.arguments` (JSON)
- `tool_result` blocks → `llm.input_messages.{i}.message.tool_call_id`

**Adapter-relevant gap:** when `system` is supplied as a top-level parameter, the instrumentation **synthesizes a system message at index 0** (`llm.input_messages.0.message.role = "system"`) and shifts the real `messages` to start at index 1. So the input-message indices do **not** line up 1:1 with the SDK's `messages` array whenever a system prompt is present. Many Anthropic content-block types (`thinking`, `redacted_thinking`, `server_tool_use`, `web_search_tool_result`, `web_fetch_tool_result`, `code_execution_tool_result`, `document`, `search_result`, `container_upload`, etc.) are explicitly **skipped** (no attributes emitted) on both the input and output paths.

Output messages are always written at index `0` only (`llm.output_messages.0.*`); a single assistant turn is assumed.

---

## Section 4 — Legacy Text Completions spans (`completions.create`)

Source: [`_wrappers.py` → `_CompletionsWrapper` / `_AsyncCompletionsWrapper`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_wrappers.py#L209-L298) and [`_stream.py` → `_Stream` / `_ResponseExtractor`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_stream.py#L103-L209)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `completions.create` | LLM | **On start:** `openinference.span.kind` (`LLM`), `llm.provider` (`anthropic`), `llm.system` (`anthropic`), `llm.model_name`, `llm.prompts` (= `[prompt]`, the single text prompt as a 1-element list), `llm.tools.{j}.tool.json_schema` (if `tools` passed), `llm.invocation_parameters`, `input.value`, `input.mime_type` (`application/json`), context attributes. **On completion (non-streaming):** `output.value` (= `response.model_dump_json()`), `output.mime_type` (`application/json`). **On completion (streaming):** `output.value` (accumulated JSON), `output.mime_type`, and `llm.output_messages` set to the concatenated completion **string** (note: a raw string, not the indexed `llm.output_messages.{i}.*` form). | `_wrappers.py` → `_CompletionsWrapper`; `_stream.py` → `_Stream` | The legacy (pre-Messages) text-completions endpoint. Uses `llm.prompts` instead of `llm.input_messages.*`, and emits **no token-count attributes** at all. Streaming returns a `_Stream` proxy that accumulates `Completion` chunks. |

---

## Section 5 — `transform` wrappers (no spans)

Source: [`_wrappers.py` → `_TransformWrapper` / `_AsyncTransformWrapper`](https://github.com/Arize-ai/openinference/blob/06c997945f003cadfad7d60e7e57061a8b156b50/python/instrumentation/openinference-instrumentation-anthropic/src/openinference/instrumentation/anthropic/_wrappers.py#L117-L148)

| Instrumented point | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `anthropic._utils._transform.transform` / `async_transform` | _(no span — internal helper)_ | none | `_wrappers.py` → `_TransformWrapper` | Wraps the SDK's request-serialization helper purely to **back-fill `invocation_parameters`** into the active span's `_Params` (so Pydantic-model request bodies that were transformed into dicts are captured). Creates no span of its own and is a no-op when no `_Params` context is active. |

---

## Summary Count

| Section | Span name(s) | OI Kind | OTel SpanKind | Span shapes |
|---|---|---|---|---|
| 1 — Messages (non-streaming) | `messages.create`, `messages.parse` (+ `beta.*`) | LLM | INTERNAL | 1 shape |
| 2 — Messages stream manager | `messages.stream` (+ `beta.*`) | LLM | INTERNAL | 1 shape |
| 3 — Messages `create(stream=True)` | `messages.create` (+ `beta.*`) | LLM | INTERNAL | 1 shape |
| 4 — Legacy completions | `completions.create` | LLM | INTERNAL | 1 shape |
| 5 — `transform` | — | _(none)_ | — | 0 (context propagation into span params only) |
| **Total** | 7 distinct names | **LLM only** | INTERNAL | **4 distinct span shapes** |

Every span emitted by this instrumentation is `openinference.span.kind = LLM`. There are **no AGENT, TOOL, CHAIN, GUARDRAIL, RETRIEVER, or EMBEDDING spans**, and no context-propagation/transport instrumentation.

---

## Notes for an adapter author

> What differs from the generic OpenInference vocabulary, and from the sibling agent-framework instrumentations documented in `openinference_telemetry_spans.md` / `openinference_openai_agents_v1.4.1_telemetry_spans.md`.

- **This is a single-call client instrumentation, not an agent instrumentation.** Each span is exactly one `client.messages.*` / `client.completions.*` HTTP round-trip. There is no run/agent root span, no tool-execution span, no handoff span. If you need an agentic trace tree, that structure must come from a parent instrumentation (e.g. the Claude Agent SDK package) — these spans will appear as leaf LLM spans under it.

- **Every span is an LLM "Send" boundary.** In the send/receive classification used elsewhere in this repo, all four span shapes are **Send** (the application calling out to the Anthropic API). `remote_label` should be derived from `llm.model_name` (e.g. `llm:<model>`). There is **no combined source+target shape** — each span is purely the outbound (source/Send) side of one API call. There is no Receive span and no `llm.system`-less root.

- **`openinference.span.kind` is always `LLM`** — no need to branch on kind. Both `llm.provider` and `llm.system` are hardcoded to `"anthropic"`.

- **`llm.model_name` source differs by path.** Non-streaming `messages.*` (Section 1) sets it twice: once from the request `model` on start, then **overwrites** it from `response.model` on completion (so it reflects the resolved model, e.g. a dated snapshot id). The two streaming paths (Sections 2–3) and legacy completions (Section 4) only set it from the **request** `model` — they never read it back from the response.

- **Token counts: prompt-token math is non-obvious.** `llm.token_count.prompt` is computed as `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` (the Anthropic `input_tokens` field alone excludes cached tokens). Don't treat `llm.token_count.prompt` as equal to the API's raw `input_tokens`.
  - Non-streaming Messages (Section 1): emits `prompt`, `completion`, and the cache details `llm.token_count.prompt_details.cache_read` / `cache_write` — but **no `llm.token_count.total`**.
  - Streaming Messages (Sections 2–3): emits `prompt`, `completion`, and `total` — but **no cache-detail breakdowns**.
  - Legacy completions (Section 4): emits **no token counts at all**.

- **Input messages are re-indexed when a system prompt is present.** A top-level `system` parameter is hoisted into a synthetic `llm.input_messages.0` with role `system`, pushing the real conversation to start at index 1. Index `i` in the attributes is therefore **not** a stable pointer into the SDK's `messages` list.

- **Output messages are always single-turn at index 0.** All assistant output is written under `llm.output_messages.0.*`; tool calls are `llm.output_messages.0.message.tool_calls.{k}.tool_call.function.name` / `.arguments` (JSON-stringified `block.input`).

- **`llm.input_messages.{i}.message.tool_calls.{t}.tool_call.id` is present on input but tool-call `id` is NOT emitted on output.** The output extractor (`_get_output_messages` / `_MessageExtractor`) emits only the tool-call function name and arguments, no `tool_call.id`. If you correlate tool requests to tool results across turns, the id is only recoverable from the input side (the next turn's `tool_use` / `tool_result` blocks).

- **Legacy completions uses a different output shape.** `completions.create` streaming sets `llm.output_messages` to a **bare completion string**, not the indexed `llm.output_messages.{i}.message.*` structure used everywhere else. It also uses `llm.prompts` (a list with the single prompt string) instead of `llm.input_messages.*`. Handle this endpoint as a special case.

- **Many content-block types are silently dropped.** `thinking` / `redacted_thinking` blocks, server-side tool results (`web_search_tool_result`, `web_fetch_tool_result`, `code_execution_tool_result`, `server_tool_use`, etc.), `document`, and `search_result` blocks emit **no attributes**. A message that is *only* thinking/tool-result content can produce a role with no content attribute. Don't assume every turn yields a `message.content`.

- **`input.value` is the verbatim request kwargs** (`safe_json_dumps(kwargs)`, mime `application/json`), and on the Messages paths `output.value` is the full `response.model_dump_json()`. These coarse blobs are the most reliable place to recover anything the per-message extractors skip.

- **Streaming spans close late.** For `messages.stream` and `create(stream=True)`, the span stays open until the caller fully drains the stream (or the context manager exits). A consumer that abandons the iterator early may leave the span without its completion attributes. The streaming completion attributes are reconstructed from the SDK's own accumulated snapshot (`accumulate_event` / `current_message_snapshot`), not from independent parsing.

- **`messages.create` and `messages.parse` are indistinguishable by kind/attributes** — only the span **name** differs. Likewise the only signal that a call went through the beta namespace is the `beta.` name prefix; attribute shapes are identical.
