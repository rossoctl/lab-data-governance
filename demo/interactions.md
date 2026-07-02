# Demo interactions (ground-truth from code)

Caller → callee pairs the demo's CODE produces during one full
`run-demo.sh` run (two A2A turns: "plan" then "book"). Grouped by anchor-
rule kind per CONTEXT.md / ADR-0009 terminology.

The flow shape (from `flow.md` and confirmed in `code/app/app_agents.py`):

```
Turn 1 (plan):
  demo-client --A2A--> travel-advisor
    travel-advisor --LLM--> claude-haiku (loops, emits tool_calls)
    travel-advisor --MCP--> {search_destinations, get_weather,
                              get_flights x2, get_hotels}
    travel-advisor --A2A--> research-agent
      research-agent --LLM--> claude-haiku  (no tools)

Turn 2 (book):
  demo-client --A2A--> travel-advisor
    travel-advisor --LLM--> claude-haiku
    travel-advisor --A2A--> booking-agent
      booking-agent --LLM--> claude-haiku (loops, emits tool_calls)
      booking-agent --MCP--> {book_flight x2, book_hotel,
                                send_notification}
```

Every agent pod has `HTTPXClientInstrumentor` + `StarletteInstrumentor`
wired (`code/kagenti/lineage/otel.py:60-61`), so all A2A and MCP HTTP
calls produce paired CLIENT/SERVER spans, and every framework runtime
has its OpenInference instrumentor attached.

## anchor-rule: `cross-service`

Boundary-crossing HTTP. Two anchors per interaction (CLIENT span on the
caller side + SERVER span on the callee side).

### A2A (JSON-RPC over HTTP) — agent-to-agent peer calls

- **`client:demo-client` → `agent:(dl-demo, travel-advisor)`**
  - Evidence: `code/app/demo.py:54-71` — `client.send_message(msg)`
    inside `for i, text in enumerate(TURNS, 1)`; CLIENT span is
    HTTPXClientInstrumentor on the demo's `httpx.AsyncClient`, SERVER
    span is StarletteInstrumentor on travel-advisor's
    `A2AStarletteApplication` (`core/a2a_server.py:113-117`).
  - Note: kicks off a turn; fires **2 times per run** (one per turn).

- **`agent:(dl-demo, travel-advisor)` → `agent:(dl-demo, booking-agent)`**
  - Evidence: peer client built in `core/a2a_server.py:68-82`
    (`_resolve_peers`); the call site is
    `core/runtimes/openai_agents.py:38-45` (`peer.client.send_message`
    inside the `delegate_to_booking_agent` FunctionTool's `invoke`).
  - Note: turn-2 delegation. Fires **once** per "book" turn.

- **`agent:(dl-demo, travel-advisor)` → `agent:(dl-demo, research-agent)`**
  - Evidence: same call site (`runtimes/openai_agents.py:38-45`); the
    delegate FunctionTool is built per peer in `_delegate(...)` at
    `runtimes/openai_agents.py:31-53`.
  - Note: turn-1 delegation. Fires **once** per "plan" turn.

### MCP (streamable HTTP) — agent-to-deployed-tool calls

Each MCP tool call also produces an `openinference-tool` anchor on the
caller side (see "ADR-0010 dual-anchor" group below); the cross-service
anchor pair is the underlying HTTP CLIENT/SERVER pair — ADR-0010 keeps
both interactions emitted, and ADR-0011's tool-merge reconciliation is
listed as a future member.

The `core/mcp_server.py` server uses FastMCP (`stateless_http=True`,
`code/kagenti/core/mcp_server.py:14-19`); MCP CLIENTs are framework-
specific (`MCPServerStreamableHttp` for openai_agents, `MultiServerMCPClient`
for langgraph). `StarletteInstrumentor` is not wired on tool pods'
FastMCP server (lineage layer only wires Starlette globally), but
HTTPXClientInstrumentor on the agent side still produces a CLIENT POST,
which `Caller inference rule` resolves to a deployed-`tool:` callee by
hostname (`<fn>-mcp`).

**Trace-evidence asymmetry between the two MCP clients (caveat):**

- `langgraph` / `MultiServerMCPClient` (booking-agent) goes through
  `httpx`, so HTTPXClientInstrumentor emits CLIENT POSTs and the
  receiving FastMCP server (with StarletteInstrumentor wired globally
  in `lineage/otel.py:61`) emits paired SERVER POSTs. Both anchor spans
  for the cross-service rule are present.

- `openai_agents` / `MCPServerStreamableHttp` (travel-advisor) does
  NOT go through `httpx` — its streamable-HTTP transport is an internal
  aiohttp-based pathway that HTTPXClientInstrumentor never sees. The
  result: zero CLIENT POSTs and zero in-trace SERVER POSTs for travel-
  advisor's MCP tool calls. The cross-service rule has no spans to
  anchor on, so the cross-service interactions below (travel-advisor
  → get_flights / get_hotels / get_weather / search_destinations) are
  **absent in this trace's prototype output**, even though the logical
  calls did happen. This is an upstream tracing gap; resolving it
  requires either (a) adding aiohttp instrumentation to dl-demo, or
  (b) the openai_agents MCP transport being reworked to use httpx.

  The deployed-tool *entities* (e.g. `tool:(dl-demo, get_flights)`)
  are still recovered from the `mcp_tools` CHAIN spans openai_agents
  emits at tool-discovery time (each one carries an `output.value`
  listing the remote tool names). See `procedure.py` `_mcp_tool_names`.

**MCP cross-service count inflation (caveat for booking-agent):**

The cross-service counts in the DB for booking-agent's MCP tools are
8/4/4 (book_flight x2 calls × 4 protocol exchanges, etc.) rather than
the 2/1/1 logical-call count below. The 4× inflation comes from
`MultiServerMCPClient` re-fetching the tool list (initialize +
tools/list) from every configured MCP server on each agent run, plus
the actual `tools/call`. This trace has zero observable signal to
distinguish protocol-level exchanges (`mcp.method` / `rpc.method` are
absent; bodies aren't captured; source ports differ per POST). The
counts below reflect *logical* calls; the inflation in the prototype
output is left as a known limitation pending one of: an MCP
instrumentor that stamps `mcp.method` on the spans; FastMCP /
langchain-mcp-adapters emitting protocol attributes; or a body-aware
post-pass on the prototype. Not fixable from the current trace data
alone.

- **`agent:(dl-demo, booking-agent)` → `tool:(dl-demo, book_flight)`**
  - Evidence: tool list `app_agents.py:42`; outbound URL constructed in
    `core/runtimes/langgraph.py:83-89`
    (`http://book-flight-mcp:8000/mcp`).
  - Note: books outbound + return flights. Fires **2 times per "book"
    turn** (deterministic per `app_agents.py:36-39`).

- **`agent:(dl-demo, booking-agent)` → `tool:(dl-demo, book_hotel)`**
  - Evidence: `app_agents.py:42`; URL pattern as above.
  - Note: books one hotel. Fires **once per "book" turn**.

- **`agent:(dl-demo, booking-agent)` → `tool:(dl-demo, send_notification)`**
  - Evidence: `app_agents.py:42`; URL pattern as above.
  - Note: confirmation email step. Fires **once per "book" turn**.

- **`agent:(dl-demo, travel-advisor)` → `tool:(dl-demo, get_flights)`**
  - Evidence: `app_agents.py:72`; URL constructed in
    `core/runtimes/openai_agents.py:70-74`.
  - Note: fetches outbound + return flight options. Fires **2 times per
    "plan" turn**.

- **`agent:(dl-demo, travel-advisor)` → `tool:(dl-demo, get_hotels)`**
  - Evidence: `app_agents.py:72`; URL pattern as above.
  - Note: fetches hotel options. Fires **once per "plan" turn**.

- **`agent:(dl-demo, travel-advisor)` → `tool:(dl-demo, get_weather)`**
  - Evidence: `app_agents.py:72`; URL pattern as above.
  - Note: fetches forecast. Fires **once per "plan" turn**.

- **`agent:(dl-demo, travel-advisor)` → `tool:(dl-demo, search_destinations)`**
  - Evidence: `app_agents.py:72`; URL pattern as above.
  - Note: fetches destination ideas. Fires **once per "plan" turn**.

## anchor-rule: `openinference-llm`

Anchored on the OpenInference LLM span emitted by each per-framework
instrumentor. Pre-reconciliation, callee is
`llm:(unknown)/claude-haiku-4-5-20251001`; ADR-0011 reconciliation
re-targets to `llm:ete-litellm.ai-models.vpc-int.res.ibm.com/...` once
paired with the cross-service `external-http` anchor (see below).

- **`agent:(dl-demo, booking-agent)` → `llm:.../claude-haiku-4-5-20251001`**
  - Evidence: `LangChainInstrumentor` (`lineage/otel.py:67-69`) wraps
    the `ChatOpenAI` instance built in
    `core/runtimes/langgraph.py:96-97`; the LLM is invoked from inside
    `langgraph.prebuilt.create_react_agent` at line 98 each call to
    `_Rt.run_turn` (`langgraph.py:65-71`).
  - Note: react-agent loop — **data-driven; depends on LLM tool-call
    count**. For the "book" turn typically 4-5 LLM calls (one per
    book_flight/book_flight/book_hotel/send_notification + final).

- **`agent:(dl-demo, research-agent)` → `llm:.../claude-haiku-4-5-20251001`**
  - Evidence: `GoogleADKInstrumentor` (`lineage/otel.py:73-75`) wraps
    `LiteLlm` built in `core/runtimes/google_sdk.py:108-109`; invoked
    from `_Rt.run_turn` (`google_sdk.py:72-84`) via
    `runner.run_async`.
  - Note: research-agent has no tools, so typically **1 LLM call per
    invocation**.

- **`agent:(dl-demo, travel-advisor)` → `llm:.../claude-haiku-4-5-20251001`**
  - Evidence: `OpenAIAgentsInstrumentor` (`lineage/otel.py:64-66`)
    wraps `OpenAIChatCompletionsModel` built in
    `core/runtimes/openai_agents.py:80-82`; invoked from
    `Runner.run(...)` in `_Rt.run_turn` (`openai_agents.py:60-62`,
    `max_turns=20`).
  - Note: agent loop — **data-driven; depends on LLM tool-call count**.
    Plan turn typically 6-7 LLM calls (5 MCP tools + 1 delegate +
    final); book turn typically 2 (1 delegate + final).

## anchor-rule: `openinference-tool`

Anchored on the OpenInference TOOL span the caller's framework
instrumentor emits when its FunctionTool / StructuredTool is invoked.
ADR-0010 says these always coexist with the `cross-service` anchor for
deployed tools (and for A2A delegations) — both interactions are
emitted; reconciliation between them is left open.

### In-process delegate tools (the `delegate_to_*` FunctionTools)

These are tagged with `kagenti.call.kind="agent_consultation"` by
`code/kagenti/lineage/delegate.py:27-31` (called as `pre_delegate_hook`
inside the FunctionTool's invoke at `runtimes/openai_agents.py:33-35`).
The `Caller inference rule` reads `gen_ai.agent.name` to bind the
callee, so it resolves to the peer **agent**, not to a tool entity —
note that the in-process tool entity above
(`tool:agent:(...):delegate_to_*`) IS still discovered as the OI TOOL
span's owner via the OpenInference tool name attribute.

- **`agent:(dl-demo, travel-advisor)` →
  `tool:agent:(dl-demo,travel-advisor):delegate_to_booking_agent`**
  - Evidence: TOOL span emitted by `OpenAIAgentsInstrumentor` over the
    `FunctionTool(name="delegate_to_booking_agent", ...)` at
    `core/runtimes/openai_agents.py:47-53`; tag attributes in
    `lineage/delegate.py:27-31`.
  - Note: turn-2 delegation. Fires **once per "book" turn**.

- **`agent:(dl-demo, travel-advisor)` →
  `tool:agent:(dl-demo,travel-advisor):delegate_to_research_agent`**
  - Evidence: same instrumentor + same FunctionTool factory; peer is
    `research`.
  - Note: turn-1 delegation. Fires **once per "plan" turn**.

### Deployed-tool MCP calls (OI TOOL span on caller side)

For each deployed MCP tool call there's a paired OI TOOL span on the
agent process (the framework's MCP-tool wrapper emits it). Per ADR-0010
this fires the `openinference-tool` anchor in addition to
`cross-service`. The callee on this anchor is the same deployed tool
NK.

- **`agent:(dl-demo, booking-agent)` → `tool:(dl-demo, book_flight)`**
  - Evidence: LangChain MCP tool wrappers exposed by
    `MultiServerMCPClient.get_tools()` at
    `core/runtimes/langgraph.py:90-92`; OI TOOL span by
    `LangChainInstrumentor` (`lineage/otel.py:67-69`).
  - Note: **2 times per "book" turn**.

- **`agent:(dl-demo, booking-agent)` → `tool:(dl-demo, book_hotel)`**
  - Evidence: same; **once per "book" turn**.

- **`agent:(dl-demo, booking-agent)` → `tool:(dl-demo, send_notification)`**
  - Evidence: same; **once per "book" turn**.

- **`agent:(dl-demo, travel-advisor)` → `tool:(dl-demo, get_flights)`**
  - Evidence: `MCPServerStreamableHttp` set up in
    `core/runtimes/openai_agents.py:69-74`; OI TOOL span by
    `OpenAIAgentsInstrumentor`.
  - Note: **2 times per "plan" turn**.

- **`agent:(dl-demo, travel-advisor)` → `tool:(dl-demo, get_hotels)`**
  - Evidence: same; **once per "plan" turn**.

- **`agent:(dl-demo, travel-advisor)` → `tool:(dl-demo, get_weather)`**
  - Evidence: same; **once per "plan" turn**.

- **`agent:(dl-demo, travel-advisor)` →
  `tool:(dl-demo, search_destinations)`**
  - Evidence: same; **once per "plan" turn**.

## anchor-rule: `external-http`

Anchored on a CLIENT HTTP span whose host doesn't match any deployed
agent / tool. In this demo only one host qualifies: the LLM gateway.
Per ADR-0011 each such CLIENT span is paired with the
sibling/descendant OpenInference LLM span (same logical call), and the
`external-http` interaction is destructively retracted once the LLM
interaction is retargeted onto the host-resolved `llm:` entity. Pre-
reconciliation the callee is the unresolved `service:` entity below.

- **`agent:(dl-demo, booking-agent)` →
  `service:ete-litellm.ai-models.vpc-int.res.ibm.com`** (then retracted,
  reconciled against the booking-agent's LLM interaction).
  - Evidence: `HTTPXClientInstrumentor` in `lineage/otel.py:60`; egress
    issued by `ChatOpenAI` (`core/runtimes/langgraph.py:96-97`) which
    uses an internal `httpx.AsyncClient` against
    `LLM_URL=https://ete-litellm.ai-models.vpc-int.res.ibm.com/v1`
    (`manifests/21-agent-booking.yaml:22`).
  - Note: **1 per LLM call** — pairs 1:1 with the booking-agent's
    `openinference-llm` interactions; data-driven count.

- **`agent:(dl-demo, research-agent)` →
  `service:ete-litellm.ai-models.vpc-int.res.ibm.com`** (then retracted).
  - Evidence: HTTPXClientInstrumentor; egress issued by `LiteLlm`
    (`core/runtimes/google_sdk.py:108-109`); same `LLM_URL`
    (`manifests/20-agent-research.yaml:22`).
  - Note: typically 1 per research-agent invocation.
  - **Caveat — uninstrumented egress in this trace.** The GoogleADK
    runtime does not produce a CLIENT span for the LLM HTTP egress
    that HTTPXClientInstrumentor can see (likely because LiteLLM uses
    its own `httpx` client built outside the instrumented module
    path, or the GoogleADKInstrumentor wrapping bypasses HTTPX's
    client init). Result: the prototype emits no `external-http`
    interaction for research-agent's LLM call, ADR-0011 reconciliation
    has nothing to pair the OI LLM interaction with, and that LLM
    interaction stays anchored on the unresolved
    `llm:(unknown)/claude-haiku-4-5-20251001` entity. ADR-0011
    §"Why" calls this exact case the canonical "uninstrumented
    egress" signal — leaving the LLM unresolved is the spec-correct
    behavior, not a prototype bug.

- **`agent:(dl-demo, travel-advisor)` →
  `service:ete-litellm.ai-models.vpc-int.res.ibm.com`** (then retracted).
  - Evidence: HTTPXClientInstrumentor; egress issued by `AsyncOpenAI`
    inside `OpenAIChatCompletionsModel`
    (`core/runtimes/openai_agents.py:80-82`); same `LLM_URL`
    (`manifests/22-agent-travel-advisor.yaml:22`).
  - Note: **1 per LLM call** — pairs 1:1 with the travel-advisor's
    `openinference-llm` interactions; data-driven count.
