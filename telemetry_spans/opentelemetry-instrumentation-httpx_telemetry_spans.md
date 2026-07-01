# OpenTelemetry spans — `opentelemetry-instrumentation-httpx`

- **Tracer / instrumentation name:** `opentelemetry.instrumentation.httpx`
- **Instrumented library:** `httpx >= 0.18.0` (`_instruments = ("httpx >= 0.18.0",)`)
- **Ref generated from:** `open-telemetry/opentelemetry-python-contrib` @ `main` — commit `7464e465e0f6e8c2ff232d4ad74e10e9e7d9f698`
- **Instrumentation family:** **B** (OpenTelemetry-contrib — generic third-party-library instrumentation; dynamic span names, attributes from semantic conventions)
- **Kill switch / disable:**
  - `HTTPXClientInstrumentor().uninstrument()` (global) or `HTTPXClientInstrumentor.uninstrument_client(client)` (per-client)
  - Exclude URLs via `OTEL_PYTHON_HTTPX_EXCLUDED_URLS` (or `OTEL_PYTHON_EXCLUDED_URLS` for all instrumentations)
  - Per-request: suppressed when `is_http_instrumentation_enabled()` is false (context key `suppress_http_instrumentation`)
- **Span-naming pattern:** Client spans are named by **HTTP method only** — `_get_default_span_name(method)` returns the sanitized method (e.g. `GET`, `POST`); an unrecognized method becomes `HTTP` (`_OTHER` → `HTTP`). httpx has no server-side route, so the `{method} {target}` form does **not** apply here — the name is just `{method}`.
- **Semantic-convention version / migration:** HTTP semconv **migration mode** (`_semconv_status = "migration"`) — controlled by `OTEL_SEMCONV_STABILITY_OPT_IN` (`http` → new, `http/dup` → old + new, unset → old). Transitions applied in code: `http.method` → `http.request.method`, `http.url` → `url.full`, `http.status_code` → `http.response.status_code`, `http.host` → `server.address`, `net.sock.peer.addr` → `network.peer.address`, `net.sock.peer.port` → `network.peer.port`, `http.flavor` → `network.protocol.version`, plus new `error.type`.

## Client transport (outbound request span)

A single CLIENT span per outbound request. Emitted through four equivalent code paths — the sync/async transport wrapper classes and the sync/async `wrap_function_wrapper` hooks on `httpx.HTTPTransport.handle_request` / `httpx.AsyncHTTPTransport.handle_async_request` — all sharing the same attribute-application helpers.

| Span name | Kind | Attributes | Source | Description |
|---|---|---|---|---|
| `{http.request.method}` (e.g. `GET`, `POST`; unknown method → `HTTP`) | CLIENT | **Request** (`_apply_request_client_attributes_to_span`): **new** — `http.request.method`, `url.full`, `server.address`, `server.port`, `network.peer.address`, `network.peer.port`; **old** — `http.method`, `http.url`, `http.scheme` (metric only). **Response** (`_apply_response_client_attributes_to_span`): `http.response.status_code` / `http.status_code`; on error status `error.type` = status code (new); `network.protocol.version` (new). **On exception:** `error.type` = exception class qualname (new). Optional captured headers `http.request.header.<name>` / `http.response.header.<name>` via `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST` / `..._CLIENT_RESPONSE`. Plus any set by `request_hook` / `response_hook` (sync or async). URLs redacted via `redact_url` (strips credentials). | `.../httpx/__init__.py` — `SyncOpenTelemetryTransport.handle_request`, `AsyncOpenTelemetryTransport.handle_async_request`, `HTTPXClientInstrumentor._handle_request_wrapper`, `HTTPXClientInstrumentor._handle_async_request_wrapper` | An outbound HTTP request made through an httpx `Client` / `AsyncClient` transport. |

## Summary

| Layer | Span count | Kind |
|---|---|---|
| Client transport (outbound request) | 1 | CLIENT |
| **Total distinct span types** | **1** | — |
