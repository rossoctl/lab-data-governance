"""Caller inference ladder: span attributes -> (kind, natural_key, display_name).

THROWAWAY prototype. Pure functions; no DB I/O. ADR-0009 split: this module
owns identity decisions, anchor_rules.py owns structural anchor decisions.

Ladder order matters — earlier rules win when their evidence is present.

Per CONTEXT.md "Natural key" term:
  user      -> user:<kagenti.user.id>
  client    -> client:<peer.service | client.address host | client.address ip>
  agent     -> agent:(<project>,<canonical_service>)
  tool i/p  -> tool:<owning_agent_natural_key>:<tool_name>
  tool dep  -> tool:(<project>,<canonical_service>)
  llm       -> llm:<host>/<model>   ('host' = '(unknown)' when absent)
  service   -> service:<hostname>
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any
from urllib.parse import urlparse

from data_governance.retrieval import Span


# ---------------------------------------------------------------------------
# Identity result
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Identity:
    kind: str  # user | client | agent | tool | llm | service
    natural_key: str
    display_name: str
    project_name: str | None
    detected_from: str  # one-line note on which ladder rung fired


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _resource_attr(span: Span, key: str) -> Any:
    return (span.resource_attributes or {}).get(key)


def _is_oi_kind(span: Span, *kinds: str) -> bool:
    return _attr(span, "openinference.span.kind") in kinds


def _is_agent_delegation(span: Span) -> bool:
    return _attr(span, "kagenti.call.kind") == "agent_consultation"


def _http_host(span: Span) -> str | None:
    """Best-effort host extraction from a CLIENT-shaped span."""
    url = _attr(span, "http.url") or _attr(span, "url.full")
    if isinstance(url, str):
        try:
            h = urlparse(url).hostname
            if h:
                return h
        except Exception:  # noqa: BLE001
            pass
    # Fall back to peer attributes.
    h = _attr(span, "server.address") or _attr(span, "net.peer.name")
    return h if isinstance(h, str) else None


def canonical_service_name(span: Span) -> str | None:
    """CONTEXT.md `Canonical service name`: service.name minus
    openinference.project.name as a prefix (with optional trailing -/_).

    The openinference project name is a *resource* attribute (set on the
    Resource, not on individual spans), so check resource_attributes first
    and fall back to span attributes for tolerance.
    """
    svc = span.service_name
    if not svc:
        return None
    project = project_name_of(span)
    if not project:
        return svc
    if not svc.startswith(project):
        return svc
    rest = svc[len(project) :]
    if rest and rest[0] in ("-", "_"):
        rest = rest[1:]
    return rest or svc  # empty after strip -> keep literal


def project_name_of(span: Span) -> str | None:
    p = _resource_attr(span, "openinference.project.name") or _attr(
        span, "openinference.project.name"
    )
    return p if isinstance(p, str) and p else None


def _llm_host_from_invocation(span: Span) -> str | None:
    params = _attr(span, "llm.invocation_parameters")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:  # noqa: BLE001
            params = None
    if isinstance(params, dict):
        url = params.get("base_url") or params.get("api_base")
        if isinstance(url, str):
            try:
                h = urlparse(url).hostname
                if h:
                    return h
            except Exception:  # noqa: BLE001
                return None
    return None


def _llm_model(span: Span) -> str | None:
    raw = _attr(span, "llm.model_name") or _attr(span, "gen_ai.request.model")
    if not isinstance(raw, str):
        return None
    # Strip litellm provider prefix (openai/<model>) — provider is routing, not identity.
    if "/" in raw:
        raw = raw.split("/", 1)[1]
    return raw


# ---------------------------------------------------------------------------
# Per-kind identifiers
# ---------------------------------------------------------------------------


def _user_from_span(span: Span) -> Identity | None:
    """Top of the caller ladder. ADR-0009 example: user→agent shape."""
    uid = _attr(span, "kagenti.user.id")
    if isinstance(uid, str) and uid:
        return Identity(
            kind="user",
            natural_key=f"user:{uid}",
            display_name=uid,
            project_name=None,
            detected_from="kagenti.user.id present",
        )
    return None


def _client_from_span(span: Span) -> Identity | None:
    """Caller-side identity for a SERVER span when no parent CLIENT span yet exists.

    Preference (per Natural-key term):
      1. peer.service
      2. client.address as hostname
      3. client.address as IP
    """
    peer = _attr(span, "peer.service")
    if isinstance(peer, str) and peer:
        return Identity(
            kind="client",
            natural_key=f"client:{peer}",
            display_name=peer,
            project_name=None,
            detected_from="peer.service",
        )
    addr = _attr(span, "client.address")
    if isinstance(addr, str) and addr:
        # Heuristic: IP if starts with digit, else hostname.
        return Identity(
            kind="client",
            natural_key=f"client:{addr}",
            display_name=addr,
            project_name=None,
            detected_from="client.address",
        )
    return None


def _agent_or_deployed_tool_from_service(span: Span) -> Identity | None:
    """For a span owned by a service, pick agent vs deployed-tool by structural hint.

    The hint we use here is the OpenInference project name + canonical service.
    Distinguishing agent from deployed-tool is left to the caller (it depends
    on what spans the service has emitted across the trace, which the
    procedure module knows). This function returns an `agent` identity by
    default; callers that have evidence of a `/mcp` SERVER on the same service
    can re-tag the natural key prefix.
    """
    if not span.service_name:
        return None
    project = project_name_of(span)
    canonical = canonical_service_name(span)
    if not canonical:
        return None
    nk = f"agent:({project or ''},{canonical})"
    return Identity(
        kind="agent",
        natural_key=nk,
        display_name=canonical,
        project_name=project,
        detected_from="service.name + openinference.project.name (canonical)",
    )


def deployed_tool_identity(span: Span) -> Identity | None:
    """As above, but tagged as deployed `tool` rather than `agent`. Callers
    that recognise the service as a tool service (e.g. /mcp SERVER seen) use
    this instead."""
    if not span.service_name:
        return None
    project = project_name_of(span)
    canonical = canonical_service_name(span)
    if not canonical:
        return None
    nk = f"tool:({project or ''},{canonical})"
    return Identity(
        kind="tool",
        natural_key=nk,
        display_name=canonical,
        project_name=project,
        detected_from="deployed tool service (canonical)",
    )


def in_process_tool_identity(
    tool_span: Span, owning_agent_nk: str
) -> Identity | None:
    """OpenInference TOOL-kind INTERNAL span -> in-process tool identity.
    Includes delegate primitives (kagenti.call.kind=agent_consultation):
    per ADR-0010 'symmetry', delegates emit an `agent → tool` interaction
    in addition to the cross-service A2A peer call.
    """
    if not _is_oi_kind(tool_span, "TOOL"):
        return None
    name = tool_span.name or "(unnamed tool)"
    return Identity(
        kind="tool",
        natural_key=f"tool:{owning_agent_nk}:{name}",
        display_name=name,
        project_name=None,
        detected_from="OpenInference TOOL span (in-process)",
    )


def llm_identity(llm_span: Span) -> Identity | None:
    """OpenInference LLM-kind INTERNAL span -> llm identity.

    Per ADR-0011: anchor rules emit eagerly without descendant lookahead.
    When `llm.invocation_parameters` carries the host, the entity is
    `llm:<host>/<model>`. Otherwise the entity is `llm:(unknown)/<model>` —
    cross-trace stable, one row per model. Reconciliation retargets the
    individual interaction onto `llm:<host>/<model>` once a paired CLIENT
    POST surfaces the host.
    """
    if not _is_oi_kind(llm_span, "LLM"):
        return None
    model = _llm_model(llm_span) or "(unknown)"
    host = _llm_host_from_invocation(llm_span)
    if host:
        nk = f"llm:{host}/{model}"
        display = f"{model} @ {host}"
    else:
        nk = f"llm:(unknown)/{model}"
        display = f"{model} (host unknown)"
    return Identity(
        kind="llm",
        natural_key=nk,
        display_name=display,
        project_name=None,
        detected_from="OpenInference LLM span",
    )


def service_identity_from_client(span: Span) -> Identity | None:
    """CLIENT span -> external `service` identity, hostname-keyed."""
    host = _http_host(span)
    if not host:
        return None
    return Identity(
        kind="service",
        natural_key=f"service:{host}",
        display_name=host,
        project_name=None,
        detected_from="external HTTP host on CLIENT span",
    )


# ---------------------------------------------------------------------------
# Ladder entry: caller for an anchor span (when no in-trace parent exists)
# ---------------------------------------------------------------------------


def infer_caller_for_orphan_server(span: Span) -> Identity:
    """For an orphan-server anchor (SERVER span with no in-trace parent),
    decide what kind of entity is calling.

    Ladder:
      1. kagenti.user.id -> user
      2. peer.service / client.address -> client
      3. fall through: provisional client with synthesized key
    """
    user = _user_from_span(span)
    if user is not None:
        return user
    client = _client_from_span(span)
    if client is not None:
        return client
    return Identity(
        kind="client",
        natural_key=f"client:(unknown):{span.span_id}",
        display_name="(unknown client)",
        project_name=None,
        detected_from="provisional — no caller evidence on SERVER span",
    )
