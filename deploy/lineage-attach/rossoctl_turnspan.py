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

import os

# Opt-out escape hatch (belt-and-suspenders; the pth already guards on it).
_DISABLED = os.environ.get("ROSSOCTL_TURNSPAN", "on").lower() in ("off", "0", "false", "no")

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
    """Patch uvicorn.Config so every server started in this interpreter wraps
    its ASGI app in TurnSpanMiddleware. Idempotent and fail-safe: any error
    leaves the app untouched (propagation still works, just possibly-fragmented
    — never worse than before).

    No-op unless LINEAGE_PROPAGATE=1 (the propagate hook's activation switch) and
    ROSSOCTL_TURNSPAN is not opted out: the turn span is worthless — and worse
    than baseline — without the activation hook, and gating here keeps an
    unactivated -otel image fully inert (no mcp/opentelemetry import at startup)."""
    if _DISABLED or not _ACTIVATED:
        return
    try:
        import uvicorn
    except Exception:
        return
    if getattr(uvicorn.Config, "_rossoctl_turnspan_patched", False):
        return
    _orig_init = uvicorn.Config.__init__

    def _patched_init(self, app, *args, **kwargs):
        try:
            if app is not None and not isinstance(app, TurnSpanMiddleware):
                # app may be an ASGI callable or an import string; only wrap
                # callables (import strings are resolved by uvicorn later — the
                # common in-process case here always passes a built app object).
                if callable(app):
                    app = TurnSpanMiddleware(app)
        except Exception:
            pass
        _orig_init(self, app, *args, **kwargs)

    try:
        uvicorn.Config.__init__ = _patched_init
        uvicorn.Config._rossoctl_turnspan_patched = True
    except Exception:
        pass

    _install_mcp_propagation()


# Per-request W3C carrier captured at send_request time (in the turn context) and
# consumed when the transport actually POSTs (in a startup-spawned background task
# whose own context predates the turn span). Keyed by JSON-RPC request id — which
# is unique per live MCP session and popped on use, so the map stays tiny.
_mcp_carriers: "dict[object, dict]" = {}


def _install_mcp_propagation():
    """Carry the current turn's traceparent onto MCP streamable-HTTP tool calls.

    WHY: mcp's ClientSession posts each request from a long-lived anyio task
    started at session-enter (`tg.start_soon` in __aenter__). anyio snapshots
    contextvars at task-creation time, so that task — and the httpx POST it
    makes — never sees the per-turn span; the httpx instrumentor then injects a
    fresh-root traceparent and every tool call lands in its own trace.

    FIX: `send_request` DOES run in the turn context. Patch it to snapshot the
    W3C headers (traceparent + tracestate) for the current context, keyed by the
    request id; patch the transport's header builder to merge that carrier onto
    the outbound POST. Fail-safe: any error leaves MCP untouched.
    """
    try:
        from mcp.shared.session import BaseSession
        from mcp.client.streamable_http import StreamableHTTPTransport
        from opentelemetry.propagate import inject, extract
        from opentelemetry import context as otel_context
    except Exception:
        return
    if getattr(BaseSession, "_rossoctl_mcp_patched", False):
        return

    # --- capture at send_request (runs in the TURN context) ---
    # We snapshot the W3C headers for the current context and key them by the
    # request id this call will use. We do NOT rely on setting a header on the
    # outgoing request: the httpx OTel instrumentor re-injects traceparent from
    # the *background task's* (startup) context and would overwrite any header we
    # set. Instead we re-ATTACH the captured context around the actual POST so
    # httpx's own injection uses the turn context — no clobber.
    _orig_send_request = BaseSession.send_request

    async def _send_request(self, request, *args, **kwargs):
        try:
            carrier: dict = {}
            inject(carrier)  # W3C traceparent/tracestate for the CURRENT context
            if carrier.get("traceparent"):
                _mcp_carriers[self._request_id] = carrier  # id this call will use
        except Exception:
            pass
        return await _orig_send_request(self, request, *args, **kwargs)

    # --- re-attach the captured context around the POST (background task) ---
    # _handle_post_request runs in the session's startup-spawned task, so the
    # turn context is not current here. Look up the carrier by this message's
    # request id, rebuild the context from it, and attach it for the POST so the
    # httpx instrumentor injects the turn's traceparent onto the wire.
    _orig_post = StreamableHTTPTransport._handle_post_request

    async def _handle_post_request(self, ctx):
        token = None
        try:
            root = getattr(getattr(ctx, "session_message", None), "message", None)
            root = getattr(root, "root", None)
            rid = getattr(root, "id", None)
            carrier = _mcp_carriers.pop(rid, None) if rid is not None else None
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
