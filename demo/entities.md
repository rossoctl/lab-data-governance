# Demo entities (ground-truth from code)

Derived by reading `data-lineage/dl_demo/code/` and confirming canonical
service names against `data-lineage/dl_demo/manifests/`. The demo runs in
the `dl-demo` k8s namespace; OTel `openinference.project.name` is
hard-coded to `dl-demo` (`code/kagenti/lineage/otel.py:41`,
`code/app/demo.py:34`), so the project component of every agent / deployed-
tool natural key is `dl-demo`.

OTel `service.name` is set by `core/dispatch.py:33` to
`f"dl-demo-{KAGENTI_NAME}"`. Per CONTEXT.md "Canonical service name", the
`dl-demo-` prefix is stripped, so the canonical service name equals
`KAGENTI_NAME` exactly.

## client

- **`client:demo-client`** — the A2A client that drives the two-turn run.
  - Display name: `demo-client`
  - Evidence: `code/app/demo.py:34` sets `SERVICE_NAME = "demo-client"` on
    the TracerProvider; `demo.py:42-71` opens `httpx.AsyncClient` and POSTs
    A2A messages to `travel-advisor` (`A1_URL`).
  - Note: there is no `kagenti.user.id` stamping anywhere, so this is a
    `client` (not a `user`) per CONTEXT.md's distinction.

## agent

All three agents run image `localhost/dl-demo/kagenti:latest` and dispatch
via `core/dispatch.py` to `core/a2a_server.serve(...)` on port 8080.

- **`agent:(dl-demo, booking-agent)`**
  - Display: `Booking Agent`
  - Evidence: `code/app/app_agents.py:21-45` declares the Agent;
    `manifests/21-agent-booking.yaml` deploys `KAGENTI_NAME=booking-agent`
    on framework `langgraph`.
- **`agent:(dl-demo, research-agent)`**
  - Display: `Research Agent`
  - Evidence: `code/app/app_agents.py:9-19` (framework `google_sdk`, no
    tools, no peers — pure LLM); `manifests/20-agent-research.yaml`.
- **`agent:(dl-demo, travel-advisor)`**
  - Display: `Travel Advisor`
  - Evidence: `code/app/app_agents.py:47-75` (framework `openai_agents`,
    4 MCP tools, 2 peers); `manifests/22-agent-travel-advisor.yaml`.

## llm

A single model is reached through one LLM gateway. All three agent
manifests set:
`LLM_URL=https://ete-litellm.ai-models.vpc-int.res.ibm.com/v1`,
`LLM_MODEL=claude-haiku-4-5-20251001`. The HTTP egress is plain
OpenAI-compatible chat completions; the gateway host fronts an upstream
Anthropic Haiku model (LiteLLM-style).

OpenInference LLM spans are emitted by per-framework instrumentors in
`code/kagenti/lineage/otel.py:64-75` (OpenAIAgentsInstrumentor for
travel-advisor, LangChainInstrumentor for booking-agent,
GoogleADKInstrumentor for research-agent). HTTPX egress is wired in the
same file at line 60.

- **`llm:(unknown)/claude-haiku-4-5-20251001`** — pre-reconciliation
  identity asserted by every OpenInference LLM span on its own (no host
  attribute on OI LLM spans).
  - Display: `claude-haiku-4-5 (unresolved host)`
  - Evidence: `manifests/2[0-2]-agent-*.yaml` LLM_MODEL env var; the LLM
    request is issued by the SDK clients in
    `code/kagenti/core/runtimes/openai_agents.py:80-82` (`AsyncOpenAI`),
    `langgraph.py:96-97` (`ChatOpenAI`), `google_sdk.py:108-109`
    (`LiteLlm`).
- **`llm:ete-litellm.ai-models.vpc-int.res.ibm.com/claude-haiku-4-5-20251001`**
  — host-resolved identity produced by ADR-0011 reconciliation when the
  paired CLIENT POST surfaces the gateway host. The unresolved-host
  entity above is destructively retracted once its last attached
  interaction retargets here.
  - Display: `claude-haiku-4-5 @ ete-litellm`
  - Evidence: `manifests/2[0-2]-agent-*.yaml` LLM_URL env var; the
    HTTPXClientInstrumentor in `lineage/otel.py:60` ensures CLIENT spans
    naming this host appear under each LLM span.

## tool — in-process (OpenInference, owned by an agent)

Each runtime wraps the agent's A2A peers as in-process FunctionTools so
the LLM can call them. These produce OpenInference TOOL spans inside the
caller's process. The owning agent is the entity that hosts the tool, so
the natural key embeds the agent's NK.

- **`tool:agent:(dl-demo,travel-advisor):delegate_to_booking_agent`**
  - Display: `delegate_to_booking_agent`
  - Evidence: `code/kagenti/core/runtimes/openai_agents.py:47-53`
    constructs the `FunctionTool(name=f"delegate_to_{peer.peer_id...}")`;
    `app_agents.py:73` lists `booking` as a peer of `travel`.
- **`tool:agent:(dl-demo,travel-advisor):delegate_to_research_agent`**
  - Display: `delegate_to_research_agent`
  - Evidence: same as above; `app_agents.py:73` lists `research` as a
    peer.

(`booking-agent` and `research-agent` declare no peers, so they have no
in-process delegate tools.)

## tool — deployed (one MCP service per pod)

Seven MCP tool deployments, one function per pod. Each is a
streamable-HTTP MCP server (`core/mcp_server.py:14-19`, FastMCP). The
canonical service name is `KAGENTI_NAME` (with underscores, e.g.
`search_destinations`) per the OTel `service.name` rule. The k8s
service is named `<dash-form>-mcp` (e.g. `search-destinations-mcp`) and
is the `host` part of the `service:` entity that the caller's HTTP
egress targets pre-reconciliation.

- **`tool:(dl-demo, book_flight)`** — Display: `book_flight`. Evidence:
  `code/app/app_tools.py:52-58`; `manifests/15-tool-book-flight.yaml`
  (`KAGENTI_NAME=book_flight`).
- **`tool:(dl-demo, book_hotel)`** — Display: `book_hotel`. Evidence:
  `code/app/app_tools.py:61-68`; `manifests/16-tool-book-hotel.yaml`.
- **`tool:(dl-demo, get_flights)`** — Display: `get_flights`. Evidence:
  `code/app/app_tools.py:26-36`; `manifests/12-tool-get-flights.yaml`.
- **`tool:(dl-demo, get_hotels)`** — Display: `get_hotels`. Evidence:
  `code/app/app_tools.py:39-49`; `manifests/14-tool-get-hotels.yaml`.
- **`tool:(dl-demo, get_weather)`** — Display: `get_weather`. Evidence:
  `code/app/app_tools.py:17-23`; `manifests/11-tool-get-weather.yaml`.
- **`tool:(dl-demo, search_destinations)`** — Display:
  `search_destinations`. Evidence: `code/app/app_tools.py:8-14`;
  `manifests/10-tool-search-destinations.yaml`.
- **`tool:(dl-demo, send_notification)`** — Display:
  `send_notification`. Evidence: `code/app/app_tools.py:71-78`;
  `manifests/13-tool-send-notification.yaml`.

## service (HTTP-egress callees, pre-reconciliation)

These are `Entity.kind = service` rows the **Caller inference rule**
emits when an HTTPX CLIENT span's host matches no known agent / tool /
LLM. The LLM-gateway one is destructively retracted once
ADR-0011 reconciliation pairs the CLIENT POST with the OpenInference LLM
span and retargets the LLM interaction onto the host-resolved
`llm:` entity.

- **`service:ete-litellm.ai-models.vpc-int.res.ibm.com`** — the LLM
  gateway. Pre-reconciliation only — destructively retracted by
  ADR-0011 LLM/HTTP-transport reconciliation once the paired LLM
  interaction's callee surfaces.
  - Display: `ete-litellm gateway`
  - Evidence: HTTPXClientInstrumentor wired in
    `code/kagenti/lineage/otel.py:60`; CLIENT POST issued by the LLM
    SDK clients in each runtime (see `runtimes/openai_agents.py:80-82`,
    `langgraph.py:96-97`, `google_sdk.py:108-109`); host comes from
    `LLM_URL` env var in `manifests/2[0-2]-agent-*.yaml`.

(No other external HTTP egress: A2A peer calls and MCP tool calls all
target in-cluster k8s services, which the **Caller inference rule**
resolves to `agent:` and `tool:(deployed)` entities, not `service:`.)
