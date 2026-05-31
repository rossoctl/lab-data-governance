# a2a-python SDK — OpenTelemetry Span Reference

**Tracer name:** `a2a-python-sdk` · **Version:** `1.0.0`  
**Kill-switch env var:** `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` (default `true`; set to any non-`true` value to disable all spans)  
**Install telemetry extras:** `pip install "a2a-sdk[telemetry]"`

## How span names are formed

| Pattern | Example |
|---|---|
| Class method decorated via `@trace_class` | `{module}.{ClassName}.{method_name}` |
| Standalone function decorated via `@trace_function()` | `{module}.{function_name}` |

## Error semantics

- `asyncio.CancelledError` and `QueueShutDown` → recorded on span via `record_exception`, status **not** set to ERROR.
- All other exceptions → `StatusCode.ERROR` + `description=str(e)`.

## Attributes

The SDK sets **no static span attributes** anywhere. Attribute enrichment is only possible by supplying a custom `attribute_extractor` callback to `trace_function` directly; none of the built-in instrumented classes use one.

---

## 1. Internal utility spans

Source: `src/a2a/server/tasks/task_manager.py`

| Span name | Kind | Attributes | Description |
|---|---|---|---|
| `a2a.server.tasks.task_manager.append_artifact_to_task` | INTERNAL | none | Mutates a `Task` object by adding, replacing, or appending artifact data carried in a `TaskArtifactUpdateEvent`. Raises `InvalidAgentResponseError` if `append=True` but no prior artifact exists for that index. |

---

## 2. Server-side spans — `SpanKind.SERVER`

### 2.1 `LegacyRequestHandler`

Source: `src/a2a/server/request_handlers/default_request_handler.py`  
Span name prefix: `a2a.server.request_handlers.default_request_handler.LegacyRequestHandler.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `on_message_send` | none | Non-streaming message send. Sets up agent execution, consumes events until a final `Message` or terminal `Task` is returned, optionally returning immediately if `return_immediately` is set. |
| `on_message_send_stream` | none | Streaming message send. Yields all agent-produced events as they arrive via SSE; continues consuming in the background on client disconnect. |
| `on_get_task` | none | Fetches a single task by ID from the task store; applies history-length filtering. Raises `TaskNotFoundError` if absent. |
| `on_list_tasks` | none | Lists tasks with optional pagination and history/artifact filtering. |
| `on_cancel_task` | none | Cancels a task via `AgentExecutor.cancel`; raises `TaskNotCancelableError` if the task is in a terminal state or the agent refuses. |
| `on_subscribe_to_task` | none | Re-attaches to a running task's event queue and yields its remaining events; per the A2A spec the current Task snapshot is emitted as the first event. |
| `on_create_task_push_notification_config` | none | Stores a push notification config for a task. Requires the agent's `pushNotifications` capability flag to be enabled. |
| `on_get_task_push_notification_config` | none | Retrieves a specific push notification config by config ID for a given task. |
| `on_list_task_push_notification_configs` | none | Returns all push notification configs registered for a task. |
| `on_delete_task_push_notification_config` | none | Deletes a specific push notification config for a task. |
| `on_get_extended_agent_card` | none | Returns the authenticated extended `AgentCard`, passing it through an optional modifier callback before returning. |

---

### 2.2 `DefaultRequestHandlerV2`

Source: `src/a2a/server/request_handlers/default_request_handler_v2.py`  
Span name prefix: `a2a.server.request_handlers.default_request_handler_v2.DefaultRequestHandlerV2.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `on_message_send` | none | Non-streaming message send using `ActiveTask.subscribe`; breaks on terminal/interrupted state or when `return_immediately` is requested. |
| `on_message_send_stream` | none | Streaming message send via `ActiveTask.subscribe`; yields all events without early termination. |
| `on_get_task` | none | Fetches and history-filters a task by ID from the task store. |
| `on_list_tasks` | none | Lists tasks with pagination and artifact/history filtering. |
| `on_cancel_task` | none | Cancels via `ActiveTaskRegistry`; wraps `InvalidParamsError` as `TaskNotCancelableError`. |
| `on_subscribe_to_task` | none | Re-attaches to an active task and yields all remaining events, starting with the current Task snapshot. |
| `on_create_task_push_notification_config` | none | Stores a push notification config; requires capability flag. |
| `on_get_task_push_notification_config` | none | Returns a push notification config by config ID. |
| `on_list_task_push_notification_configs` | none | Lists all push notification configs for a task. |
| `on_delete_task_push_notification_config` | none | Deletes a push notification config for a task. |
| `on_get_extended_agent_card` | none | Returns the extended `AgentCard`, passing it through `extended_card_modifier` if configured. |

---

### 2.3 `JsonRpcDispatcher`

Source: `src/a2a/server/routes/jsonrpc_dispatcher.py`  
Span name prefix: `a2a.server.routes.jsonrpc_dispatcher.JsonRpcDispatcher.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `handle_requests` | none | Top-level JSON-RPC entry point. Parses and validates the request body, routes by method name, and returns either a `JSONResponse` or an `EventSourceResponse` for streaming calls. |

*All other methods on this class begin with `_` and are excluded from tracing by `trace_class`.*

---

### 2.4 `RestDispatcher`

Source: `src/a2a/server/routes/rest_dispatcher.py`  
Span name prefix: `a2a.server.routes.rest_dispatcher.RestDispatcher.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `on_message_send` | none | Parses the request body as `SendMessageRequest`, delegates to the handler, and returns a `JSONResponse`. |
| `on_message_send_stream` | none | Parses the request body and streams agent-produced events as SSE via `EventSourceResponse`. |
| `on_get_task` | none | Extracts task ID from path params and query string, calls the handler, returns `JSONResponse`. |
| `on_cancel_task` | none | Extracts task ID from path params, cancels the task, returns `JSONResponse`. |
| `on_subscribe_to_task` | none | Extracts task ID from path params and streams remaining task events as SSE. |
| `list_tasks` | none | Parses query params as `ListTasksRequest`, calls the handler, returns `JSONResponse`. |
| `handle_authenticated_agent_card` | none | Calls the handler to obtain the extended `AgentCard` and returns it serialized as JSON. |
| `set_push_notification` | none | Parses body as `TaskPushNotificationConfig`, stores it, returns the config as JSON. |
| `get_push_notification` | none | Fetches a push notification config by task ID and config ID from path params. |
| `delete_push_notification` | none | Deletes a push notification config identified by task ID and config ID from path params. |
| `list_push_notifications` | none | Lists all push notification configs for a task identified by task ID in the path. |

---

### 2.5 `EventQueueLegacy`

Source: `src/a2a/server/events/event_queue.py`  
Span name prefix: `a2a.server.events.event_queue.EventQueueLegacy.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `enqueue_event` | none | Puts an event onto the queue and forwards it to all child (tapped) queues; silently drops the event if the queue is already closed or shut down. |
| `dequeue_event` | none | Blocking dequeue from the queue; raises `QueueShutDown` if the queue is closed and empty. |
| `task_done` | none | Signals the underlying `asyncio.Queue` that a previously dequeued item has been fully processed. |
| `tap` | none | Creates and registers a child queue that receives all future events enqueued to this source. Returns the new child `EventQueueLegacy`. |
| `close` | none | Closes the queue; if `immediate=True`, flushes and unblocks consumers immediately, otherwise drains gracefully. |
| `is_closed` | none | Returns `True` if the queue has been closed, `False` otherwise. |

---

### 2.6 `EventQueueSource`

Source: `src/a2a/server/events/event_queue_v2.py`  
Span name prefix: `a2a.server.events.event_queue_v2.EventQueueSource.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `enqueue_event` | none | Puts an event on the internal incoming queue for asynchronous fan-out dispatch to all registered sinks. |
| `dequeue_event` | none | Dequeues from the default sink for backward compatibility with single-consumer code. |
| `task_done` | none | Signals task done on the default sink. |
| `tap` | none | Creates a new `EventQueueSink`, registers it for fan-out dispatch, and returns it. Raises `QueueShutDown` if the source is already closed. |
| `remove_sink` | none | Unregisters and removes a sink from the fan-out dispatch list. |
| `close` | none | Closes the source and all registered sinks; if `immediate=True` cancels the dispatcher loop immediately. |
| `is_closed` | none | Returns whether the source is closed. Deprecated; retained for backward compatibility. |
| `test_only_join_incoming_queue` | none | Test helper: waits (blocks) until all events currently in the incoming queue have been fully dispatched to all sinks. |

---

### 2.7 `EventConsumer`

Source: `src/a2a/server/events/event_consumer.py`  
Span name prefix: `a2a.server.events.event_consumer.EventConsumer.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `consume_all` | none | Async generator: polls the queue with a 0.5 s timeout, yielding events; automatically closes the queue and stops on a final event (`Message`, terminal `Task`, or terminal `TaskStatusUpdateEvent`). Re-raises any exception stored by `agent_task_callback`. |
| `agent_task_callback` | none | Done-callback attached to the agent's asyncio task; captures any raised exception so `consume_all` can re-raise it rather than hanging indefinitely. |

---

### 2.8 `InMemoryQueueManager`

Source: `src/a2a/server/events/in_memory_queue_manager.py`  
Span name prefix: `a2a.server.events.in_memory_queue_manager.InMemoryQueueManager.`

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `add` | none | Registers a new `EventQueueLegacy` for a given task ID. Raises `TaskQueueExists` if an entry already exists. |
| `get` | none | Returns the queue registered for a task ID, or `None` if no queue exists. |
| `tap` | none | Creates a child queue tapping the existing queue for a task ID; returns `None` if no queue is found. |
| `close` | none | Closes and removes the queue for a task ID. Raises `NoTaskQueue` if not found. |
| `create_or_tap` | none | Creates a fresh queue if none exists for the task ID; otherwise taps (creates a child of) the existing queue. |

---

## 3. Client-side spans — `SpanKind.CLIENT`

All three transport classes expose the same public API. The span name prefix and underlying protocol differ; the behaviour of each method is identical across transports unless noted.

### 3.1 `JsonRpcTransport`

Source: `src/a2a/client/transports/jsonrpc.py`  
Span name prefix: `a2a.client.transports.jsonrpc.JsonRpcTransport.`  
Protocol: **JSON-RPC over HTTP/SSE**

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `send_message` | none | POSTs a `SendMessage` JSON-RPC request; returns a parsed `SendMessageResponse`. |
| `send_message_streaming` | none | POSTs a `SendStreamingMessage` JSON-RPC request; yields SSE-parsed `StreamResponse` objects as they arrive. |
| `get_task` | none | Sends a `GetTask` JSON-RPC request; returns the `Task` object. |
| `list_tasks` | none | Sends a `ListTasks` JSON-RPC request; returns a `ListTasksResponse`. |
| `cancel_task` | none | Sends a `CancelTask` JSON-RPC request; returns the updated `Task`. |
| `subscribe` | none | Sends a `SubscribeToTask` streaming JSON-RPC request; yields task events as `StreamResponse` objects. |
| `get_extended_agent_card` | none | Returns the cached `AgentCard` if the `extendedAgentCard` capability is disabled; otherwise sends a `GetExtendedAgentCard` JSON-RPC request. |
| `create_task_push_notification_config` | none | Sends `CreateTaskPushNotificationConfig`; returns the stored config. |
| `get_task_push_notification_config` | none | Sends `GetTaskPushNotificationConfig`; returns the matching config. |
| `list_task_push_notification_configs` | none | Sends `ListTaskPushNotificationConfigs`; returns a `ListTaskPushNotificationConfigsResponse`. |
| `delete_task_push_notification_config` | none | Sends `DeleteTaskPushNotificationConfig`; returns nothing on success. |
| `close` | none | Closes the underlying `httpx.AsyncClient`. |

---

### 3.2 `GrpcTransport`

Source: `src/a2a/client/transports/grpc.py`  
Span name prefix: `a2a.client.transports.grpc.GrpcTransport.`  
Protocol: **gRPC**

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `create` | none | Class method factory: creates a `GrpcTransport` instance using the channel factory from `ClientConfig`. |
| `send_message` | none | Calls `stub.SendMessage` unary gRPC; returns `SendMessageResponse`. |
| `send_message_streaming` | none | Calls `stub.SendStreamingMessage` server-streaming gRPC; yields `StreamResponse` objects. |
| `get_task` | none | Calls `stub.GetTask` unary gRPC; returns the `Task`. |
| `list_tasks` | none | Calls `stub.ListTasks` unary gRPC; returns `ListTasksResponse`. |
| `cancel_task` | none | Calls `stub.CancelTask` unary gRPC; returns the updated `Task`. |
| `subscribe` | none | Calls `stub.SubscribeToTask` server-streaming gRPC; yields task events. |
| `get_extended_agent_card` | none | Returns the cached card if capability is off; otherwise calls `stub.GetExtendedAgentCard` unary gRPC. |
| `create_task_push_notification_config` | none | Calls `stub.CreateTaskPushNotificationConfig` unary gRPC. |
| `get_task_push_notification_config` | none | Calls `stub.GetTaskPushNotificationConfig` unary gRPC. |
| `list_task_push_notification_configs` | none | Calls `stub.ListTaskPushNotificationConfigs` unary gRPC. |
| `delete_task_push_notification_config` | none | Calls `stub.DeleteTaskPushNotificationConfig` unary gRPC; returns nothing. |
| `close` | none | Closes the gRPC channel. |

---

### 3.3 `RestTransport`

Source: `src/a2a/client/transports/rest.py`  
Span name prefix: `a2a.client.transports.rest.RestTransport.`  
Protocol: **REST over HTTP/SSE**

| Span name (suffix) | Attributes | Description |
|---|---|---|
| `send_message` | none | POSTs to `/message:send`; returns parsed `SendMessageResponse`. |
| `send_message_streaming` | none | POSTs to `/message:stream`; yields SSE-parsed `StreamResponse` objects. |
| `get_task` | none | GETs `/tasks/{id}`; returns the `Task`. |
| `list_tasks` | none | GETs `/tasks`; returns `ListTasksResponse`. |
| `cancel_task` | none | POSTs to `/tasks/{id}:cancel`; returns the updated `Task`. |
| `subscribe` | none | POSTs to `/tasks/{id}:subscribe`; yields remaining task events as SSE. |
| `get_extended_agent_card` | none | Returns cached card if capability is off; otherwise GETs `/extendedAgentCard`. |
| `create_task_push_notification_config` | none | POSTs to `/tasks/{task_id}/pushNotificationConfigs`; returns the stored config. |
| `get_task_push_notification_config` | none | GETs `/tasks/{task_id}/pushNotificationConfigs/{id}`; returns the matching config. |
| `list_task_push_notification_configs` | none | GETs `/tasks/{task_id}/pushNotificationConfigs`; returns all configs. |
| `delete_task_push_notification_config` | none | DELETEs `/tasks/{task_id}/pushNotificationConfigs/{id}`; returns nothing. |
| `close` | none | Closes the underlying `httpx.AsyncClient`. |

---

## Summary count

| Layer | Classes | Spans |
|---|---|---|
| Internal utility | 1 function | 1 |
| Server — request handlers | 2 | 22 |
| Server — dispatchers | 2 | 12 |
| Server — event queues | 2 | 14 |
| Server — event consumer | 1 | 2 |
| Server — queue manager | 1 | 5 |
| Client — transports | 3 | 38 |
| **Total** | **12** | **~94** |
