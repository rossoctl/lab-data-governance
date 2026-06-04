# opentelemetry-instrumentation-asgi — OpenTelemetry Span Reference

**Source:** `opentelemetry/instrumentation/asgi/__init__.py`  
**Package:** `opentelemetry-instrumentation-asgi`  
**Tracer name:** set by the instrumentation at middleware construction time

## Semconv stability modes

Controlled by env var `OTEL_SEMCONV_STABILITY_OPT_IN`:

| Mode | Behaviour |
|---|---|
| _(unset / default)_ | Old semconv attribute names (`http.method`, `http.url`, etc.) |
| `http` | New stable semconv names (`http.request.method`, `url.full`, etc.) |
| `http/dup` | Both sets emitted simultaneously |

## Span exclusions

Individual receive/send spans can be suppressed by passing `exclude_spans=["receive"]` or `exclude_spans=["send"]` to `OpenTelemetryMiddleware`.

---

## Spans

### 1. Server span

| Field | Value |
|---|---|
| **Span name** | `"{METHOD} {path}"` for HTTP (e.g. `GET /users`); `"{path}"` for WebSocket; `"{METHOD}"` if no route path. Overridable via `default_span_details` constructor arg. |
| **SpanKind** | `SERVER` (falls back to `INTERNAL` when there is an existing parent context) |
| **Source line** | ~756 in `OpenTelemetryMiddleware.__call__` |
| **Description** | Covers the entire lifetime of one inbound HTTP request or WebSocket connection, from first byte received to final response body/trailer flushed. |

**Attributes:**

| Attribute (old semconv) | Attribute (new semconv) | Source | Notes |
|---|---|---|---|
| `http.scheme` | `url.scheme` | `scope["scheme"]` | `http` or `https` |
| `http.host` | `server.address` | `scope["server"][0]` or `host` header | |
| `net.host.port` | `server.port` | `scope["server"][1]` | Listening port |
| `http.flavor` | `network.protocol.version` | `scope["http_version"]` | e.g. `1.1`, `2` |
| `http.target` | `url.path` + `url.query` | `scope["path"]` + `scope["query_string"]` | New semconv splits into two keys |
| `http.url` | `url.full` | Constructed from scheme + host + path + query | |
| `http.method` | `http.request.method` | `scope["method"]` | Sanitized; original preserved in `http.request.method_original` if changed |
| `http.request.method_original` | `http.request.method_original` | `scope["method"]` | Only when original differs from sanitized form |
| `http.server_name` | _(not emitted in new)_ | `host` header | Old semconv only |
| `http.user_agent` | `user_agent.original` | `user-agent` header | |
| `user_agent.synthetic.type` | `user_agent.synthetic.type` | `user-agent` header (parsed) | Only when a synthetic agent pattern is detected |
| `net.peer.ip` | `client.address` | `scope["client"][0]` | Client IP |
| `net.peer.port` | `client.port` | `scope["client"][1]` | Client port |
| `http.status_code` | `http.response.status_code` | `message["status"]` from `http.response.start` event | Set when the response is sent |
| `http.request.header.<name>` | `http.request.header.<name>` | `scope["headers"]` | Only when `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST` is set; `-` → `_`; value is `list[str]` |
| `http.response.header.<name>` | `http.response.header.<name>` | `message["headers"]` from `http.response.start` | Only when `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE` is set |

---

### 2. Receive span

| Field | Value |
|---|---|
| **Span name** | `"{server_span_name} {scope['type']} receive"` — e.g. `"GET /foo http receive"` |
| **SpanKind** | `INTERNAL` |
| **Source line** | ~889 inside `otel_receive()` closure in `_get_otel_receive()` |
| **Description** | Represents one invocation of the ASGI `receive()` callable — one incoming message chunk read from the client (request body chunk, WebSocket frame, disconnect signal, etc.). |

**Attributes:**

| Attribute | Value | Condition |
|---|---|---|
| `asgi.event.type` | `message["type"]` (e.g. `http.request`, `websocket.receive`, `websocket.disconnect`) | Always |
| `http.status_code` / `http.response.status_code` | `200` | Only when `message["type"] == "websocket.receive"` |

---

### 3. Send span

| Field | Value |
|---|---|
| **Span name** | `"{server_span_name} {scope['type']} send"` — e.g. `"GET /foo http send"` |
| **SpanKind** | `INTERNAL` |
| **Source line** | ~920 inside `_set_send_span()`, called from `otel_send()` in `_get_otel_send()` |
| **Description** | Represents one invocation of the ASGI `send()` callable — one outgoing message chunk written to the client (response headers, response body chunk, WebSocket frame). |

**Attributes:**

| Attribute | Value | Condition |
|---|---|---|
| `asgi.event.type` | `message["type"]` (e.g. `http.response.start`, `http.response.body`, `websocket.send`) | Always |
| `http.status_code` / `http.response.status_code` | Integer status code from `message["status"]` | When `message["type"] == "http.response.start"` or `"websocket.send"` (hardcoded `200` for WebSocket) |

---

## Summary

| # | Span name pattern | Kind | Key attributes |
|---|---|---|---|
| 1 | `{METHOD} {path}` / `{path}` / `{METHOD}` | SERVER | `http.method`, `http.url`, `http.scheme`, `http.host`, `net.host.port`, `http.flavor`, `http.target`, `http.user_agent`, `user_agent.synthetic.type`, `net.peer.ip`, `net.peer.port`, `http.status_code`, `http.request.header.*`, `http.response.header.*` (+ new semconv equivalents) |
| 2 | `{server_span_name} {type} receive` | INTERNAL | `asgi.event.type`, `http.status_code` (WebSocket only) |
| 3 | `{server_span_name} {type} send` | INTERNAL | `asgi.event.type`, `http.status_code` (on response start / WebSocket send) |
