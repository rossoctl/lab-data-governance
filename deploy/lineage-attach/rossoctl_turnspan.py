"""rossoctl turn-span shim (deploy-time, app-source-free).

WHY THIS EXISTS
---------------
The propagate-only OTel shim makes each individual outbound httpx call carry a
`traceparent`. But agent frameworks (openai-agents, google-sdk/adk, langgraph,
crewai) run a turn as a *loop* of model + tool steps, and several of them create
a fresh asyncio task per step. The OTel current-span is contextvars-based, so a
step that starts without an ambient span begins a NEW root trace — so one agent
turn emits MANY different traceparent trace-ids (one per tool/peer/LLM call).
The lineage sidecar faithfully mirrors that: N little 2-span traces per turn
instead of one linked tree.

THE FIX
-------
Open ONE span at the ASGI request boundary and keep it active for the entire
request scope — including the SSE streaming body, where the framework's detached
executor task runs. Every outbound call in that turn then inherits ONE trace-id.
The span is a CHILD of the extracted inbound W3C context, so when a caller's
turn trace-id arrives on the wire, this pod's turn (and its outbound calls)
continues on the same trace-id — the lineage sidecar's `dg-parent` chain then
keeps the whole multi-pod run in a single trace.

This is framework-agnostic: it is a plain ASGI middleware, auto-inserted by
patching `uvicorn.Config` (the one server both the a2a-sdk agents and the
FastMCP tool pods hand their built app to). No app source is touched. Spans are
never exported (the Deployment runs `--traces_exporter none`); only the
traceparent header propagates, exactly like the rest of this shim.

Installed at interpreter startup via `rossoctl_turnspan.pth`.
"""
from __future__ import annotations

from copy import copy
import os

# The turn span is BOUND to the propagate hook's activation switch. Rationale
# (ADR-0033 "One trace needs two shims"): a turn span WITHOUT the activation
# hook's initialize() — i.e. httpx instrumentation OFF — fragments a turn WORSE
# than the no-shim baseline (27 vs 11). Gating install() on LINEAGE_PROPAGATE=1
# makes that trap unreachable: when activation is off the turn span never patches
# anything. It also keeps the -otel image INERT with the gate off — install()
# imports mcp + opentelemetry to patch the MCP transport, and an image that
# bundles mcp would otherwise pull opentelemetry in at interpreter start and
# break build-otel-shim.sh's verify_inert attestation ("gate off, yet otel
# loaded"). No LINEAGE_PROPAGATE → install() returns before importing anything.
_ACTIVATED = os.environ.get("LINEAGE_PROPAGATE") == "1"


class TurnSpanMiddleware:
    """Wrap one span around the whole HTTP request scope, seeded from the
    inbound W3C trace context. Non-http scopes (lifespan, websocket) pass
    through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        # Imports are deferred to call time so a missing OTel (shim-off build)
        # degrades to a transparent pass-through rather than an import error.
        try:
            from opentelemetry import trace, context as otel_context
            from opentelemetry.propagate import extract
        except Exception:
            return await self.app(scope, receive, send)

        headers = {}
        for k, v in scope.get("headers", []) or []:
            try:
                headers[k.decode("latin1").lower()] = v.decode("latin1")
            except Exception:
                pass
        parent_ctx = extract(headers)  # W3C tracecontext + baggage carrier

        tracer = trace.get_tracer("rossoctl.turnspan")
        span = tracer.start_span("agent.turn", context=parent_ctx)
        token = otel_context.attach(trace.set_span_in_context(span, parent_ctx))
        try:
            await self.app(scope, receive, send)
        finally:
            otel_context.detach(token)
            span.end()


def install():
    """Patch uvicorn.Config.load so every server wraps its resolved ASGI app.

    Uvicorn resolves import strings and app factories in load(), after Config's
    constructor runs. Wrapping the loaded app also covers those entrypoints.
    Idempotent and fail-safe: any error leaves the app untouched (propagation
    still works, just possibly-fragmented — never worse than before).

    No-op unless LINEAGE_PROPAGATE=1 (the propagate hook's activation switch):
    the turn span is worthless — and worse than baseline — without the activation
    hook, and gating here keeps an unactivated -otel image fully inert (no
    mcp/opentelemetry import at startup)."""
    if not _ACTIVATED:
        return
    try:
        import uvicorn
    except Exception:
        return
    if getattr(uvicorn.Config, "_rossoctl_turnspan_patched", False):
        return
    _orig_load = uvicorn.Config.load

    def _patched_load(self):
        _orig_load(self)
        try:
            if not isinstance(self.app, TurnSpanMiddleware) and not isinstance(
                self.loaded_app, TurnSpanMiddleware
            ):
                self.loaded_app = TurnSpanMiddleware(self.loaded_app)
        except Exception:
            pass

    try:
        uvicorn.Config.load = _patched_load
        uvicorn.Config._rossoctl_turnspan_patched = True
    except Exception:
        pass

    _install_mcp_propagation()


def _install_mcp_propagation():
    """Carry the current turn's traceparent onto MCP streamable-HTTP tool calls.

    WHY: mcp's ClientSession posts each request from a long-lived anyio task
    started at session-enter (`tg.start_soon` in __aenter__). anyio snapshots
    contextvars at task-creation time, so that task — and the httpx POST it
    makes — never sees the per-turn span; the httpx instrumentor then injects a
    fresh-root traceparent and every tool call lands in its own trace.

    FIX: `send_request` DOES run in the turn context. Attach its W3C carrier to
    the metadata that travels with that exact SessionMessage into the transport
    task. Request IDs are only unique within a session, so a process-wide map
    keyed by ID can join unrelated turns. Fail-safe: any error leaves MCP
    untouched.
    """
    try:
        from mcp.shared.message import ClientMessageMetadata
        from mcp.shared.session import BaseSession
        from mcp.client.streamable_http import StreamableHTTPTransport
        from opentelemetry.propagate import inject, extract
        from opentelemetry import context as otel_context
    except Exception:
        return
    if getattr(BaseSession, "_rossoctl_mcp_patched", False):
        return

    # --- capture at send_request (runs in the TURN context) ---
    # We snapshot the W3C headers on request-local metadata. We do NOT rely on
    # setting a header on the outgoing request: the httpx OTel instrumentor
    # re-injects traceparent from the *background task's* (startup) context and
    # would overwrite it. Instead we re-ATTACH the captured context around the
    # actual POST so httpx's own injection uses the turn context — no clobber.
    _orig_send_request = BaseSession.send_request

    async def _send_request(self, request, *args, **kwargs):
        try:
            carrier: dict = {}
            inject(carrier)  # W3C traceparent/tracestate for the CURRENT context
            if carrier.get("traceparent"):
                # BaseSession.send_request(request, result_type, timeout,
                # metadata, ...). Copy caller metadata: it may be reused for
                # another request, and its resumability callbacks must survive.
                supplied = kwargs.get("metadata") if "metadata" in kwargs else (
                    args[2] if len(args) > 2 else None
                )
                metadata = copy(supplied) if supplied is not None else ClientMessageMetadata()
                metadata._rossoctl_trace_carrier = carrier
                if len(args) > 2:
                    args = (*args[:2], metadata, *args[3:])
                else:
                    kwargs["metadata"] = metadata
        except Exception:
            pass
        return await _orig_send_request(self, request, *args, **kwargs)

    # --- re-attach the captured context around the POST (background task) ---
    # _handle_post_request runs in the session's startup-spawned task, so the
    # turn context is not current here. Its SessionMessage metadata is the same
    # object captured above, so no ID lookup or global carrier lifetime exists.
    # Attach it for the POST so httpx injects the turn's traceparent on the wire.
    _orig_post = StreamableHTTPTransport._handle_post_request

    async def _handle_post_request(self, ctx):
        token = None
        try:
            carrier = getattr(getattr(ctx, "metadata", None), "_rossoctl_trace_carrier", None)
            if carrier:
                turn_ctx = extract(carrier)
                token = otel_context.attach(turn_ctx)
        except Exception:
            token = None
        try:
            return await _orig_post(self, ctx)
        finally:
            if token is not None:
                try:
                    otel_context.detach(token)
                except Exception:
                    pass

    try:
        BaseSession.send_request = _send_request
        StreamableHTTPTransport._handle_post_request = _handle_post_request
        BaseSession._rossoctl_mcp_patched = True
    except Exception:
        pass
