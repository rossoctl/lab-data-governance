# OpenTelemetry spans — `opentelemetry-instrumentation-starlette`

- **Tracer / instrumentation name:** `opentelemetry.instrumentation.starlette`
- **Instrumented library:** `starlette >= 0.13` (`_instruments = ("starlette >= 0.13",)`)
- **Ref generated from:** `open-telemetry/opentelemetry-python-contrib` @ `main` — commit `7464e465e0f6e8c2ff232d4ad74e10e9e7d9f698`
- **Instrumentation family:** **B** (OpenTelemetry-contrib — generic third-party-library instrumentation; dynamic span names, attributes from semantic conventions)
- **Schema URL:** `https://opentelemetry.io/schemas/1.11.0`
- **Kill switch / disable:**
  - `StarletteInstrumentor().uninstrument()` (global) or `StarletteInstrumentor.uninstrument_app(app)` (per-app)
  - Exclude URLs via `OTEL_PYTHON_STARLETTE_EXCLUDED_URLS` (or `OTEL_PYTHON_EXCLUDED_URLS` for all instrumentations)
- **Span-naming pattern:** Starlette only supplies `_get_default_span_details` → the actual spans are started by the shared **`opentelemetry-instrumentation-asgi`** `OpenTelemetryMiddleware`.
  - HTTP request (method + route resolved): `{method} {http.route}` (e.g. `GET /foobar`)
  - WebSocket (route only, no method): `{http.route}`
  - Fallback (route could not be resolved): `{method}`
- **Semantic-convention version / migration:** HTTP semconv **migration mode** — attribute set depends on `OTEL_SEMCONV_STABILITY_OPT_IN` (`http` → new names, `http/dup` → both, unset → old names). Old: `http.method`, `http.url`, `http.status_code`. New (stable HTTP semconv): `http.request.method`, `url.path`, `http.route`, `http.response.status_code`.

> **Note on `http.route`:** Starlette has no public way to expose `http.route` via the ASGI scope, so the instrumentation resolves it itself by matching `app.routes` against the scope (`_get_route_details`). If a route matches, `http.route` is set and used in the span name; otherwise the span falls back to just the method.

## Server request handler (ASGI SERVER span)

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{method} {http.route}` (HTTP) · `{http.route}` (WebSocket) · `{method}` (fallback) | SERVER | From HTTP/ASGI semantic conventions via `collect_request_attributes` (semconv migration): **new** — `http.request.method`, `url.path`, `url.scheme`, `url.query`, `http.route`, `http.response.status_code`, `server.address`, `server.port`, `client.address`, `client.port`, `network.protocol.version`, `user_agent.original`, `error.type`; **old** — `http.method`, `http.url`, `http.scheme`, `http.host`, `http.target`, `http.server_name`, `http.flavor`, `http.status_code`, `net.peer.ip`, `net.peer.port`, `http.user_agent`. Plus `http.route` set by Starlette's `_get_default_span_details`. Optional captured headers `http.request.header.<name>` / `http.response.header.<name>` via `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST` / `..._SERVER_RESPONSE`. Plus any set by `server_request_hook`. | `.../starlette/__init__.py` (`_get_default_span_details`, `_get_route_details`); spans started in `.../asgi/__init__.py` (`OpenTelemetryMiddleware.__call__`) | An inbound HTTP request (or WebSocket connection) handled by the Starlette application. |

## ASGI event sub-spans (INTERNAL)

These are emitted by the underlying ASGI middleware as children of the SERVER span, once per ASGI `receive` / `send` event. They can be suppressed by the middleware's `exclude_receive_span` / `exclude_send_span` options.

> **See [`asgi_telemetry_spans.md`](asgi_telemetry_spans.md)** for the authoritative definition of these sub-spans, the full semconv attribute list, and the semconv stability modes. Starlette does not re-document that shared layer — it only overrides the SERVER span name (`{method} {http.route}`) via `_get_default_span_details`.

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{server_span_name} http receive` · `{server_span_name} websocket receive` | INTERNAL | `asgi.event.type` (the ASGI message `type`, e.g. `http.request`, `websocket.receive`); for `websocket.receive`, status code 200 recorded. Plus any set by `client_request_hook`. | `.../asgi/__init__.py` (`_get_otel_receive`) | One ASGI `receive` event (the app pulling an inbound message from the server). |
| `{server_span_name} http send` · `{server_span_name} websocket send` | INTERNAL | `asgi.event.type` (e.g. `http.response.start`, `http.response.body`, `websocket.send`); HTTP/WS response status code recorded when present. Plus any set by `client_response_hook`. | `.../asgi/__init__.py` (`_get_otel_send`) | One ASGI `send` event (the app pushing an outbound message to the server). |

## Summary

| Layer | Span count | Kind |
|---|---|---|
| Server request handler | 1 | SERVER |
| ASGI `receive` sub-span | 1 (per receive event, optional) | INTERNAL |
| ASGI `send` sub-span | 1 (per send event, optional) | INTERNAL |
| **Total distinct span types** | **3** | — |
