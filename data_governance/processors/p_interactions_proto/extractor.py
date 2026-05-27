"""Pure extractor: spans -> (entities, interactions, interaction_spans, payloads).

THROWAWAY prototype. No DB I/O. Takes a list of spans (in seq order),
returns four lists of dicts. The CLI driver writes them to scratch tables.

See README.md for the assumptions encoded here.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import urlparse

from data_governance.retrieval import Span

# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProtoEntity:
    id: str
    kind: str  # user | external_client | agent | tool | external_service | llm
    natural_key: str
    display_name: str
    detected_from: str  # one-line note on how identity was decided


@dataclasses.dataclass
class ProtoInteraction:
    id: str
    caller_entity_id: str
    callee_entity_id: str
    started_at: Any
    ended_at: Any
    error: bool | None
    request_payload_hash: str | None
    response_payload_hash: str | None
    summary: str


@dataclasses.dataclass
class ProtoInteractionSpan:
    interaction_id: str
    trace_id: str
    span_id: str
    is_anchor: bool


@dataclasses.dataclass
class ProtoPayload:
    content_hash: str
    content_kind: str
    content: Any
    byte_size: int


@dataclasses.dataclass
class ExtractResult:
    entities: list[ProtoEntity]
    interactions: list[ProtoInteraction]
    interaction_spans: list[ProtoInteractionSpan]
    payloads: list[ProtoPayload]
    notes: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _service(span: Span) -> str:
    return span.service_name or "(unknown)"


def _is_oi_kind(span: Span, *kinds: str) -> bool:
    k = _attr(span, "openinference.span.kind")
    return k in kinds


def _has_oi_kind_in_service(spans_by_service: dict[str, list[Span]], service: str, *kinds: str) -> bool:
    return any(_is_oi_kind(s, *kinds) for s in spans_by_service.get(service, []))


def _http_url_host(span: Span) -> str | None:
    url = _attr(span, "http.url") or _attr(span, "url.full")
    if not url:
        return None
    if isinstance(url, str):
        try:
            return urlparse(url).hostname
        except Exception:  # noqa: BLE001
            return None
    return None


def _is_agent_delegation(span: Span) -> bool:
    """True for OpenInference TOOL spans that are agent-to-agent delegations."""
    return _attr(span, "kagenti.call.kind") == "agent_consultation"


def _llm_host_for(span: Span, all_spans: list[Span]) -> str | None:
    """Find the LLM endpoint host for a generation/LLM-kind span.

    Order of fallbacks:
      1. llm.invocation_parameters.base_url on the span itself
      2. http.url on any descendant CLIENT span
    """
    params = _attr(span, "llm.invocation_parameters")
    if isinstance(params, dict):
        url = params.get("base_url")
        if isinstance(url, str):
            try:
                h = urlparse(url).hostname
                if h:
                    return h
            except Exception:  # noqa: BLE001
                pass
    elif isinstance(params, str):
        try:
            decoded = json.loads(params)
            if isinstance(decoded, dict):
                url = decoded.get("base_url")
                if isinstance(url, str):
                    h = urlparse(url).hostname
                    if h:
                        return h
        except Exception:  # noqa: BLE001
            pass

    for ev in _walk_descendants(all_spans, span):
        if ev.kind == "CLIENT":
            h = _http_url_host(ev)
            if h:
                return h
    return None


def _llm_model_for(span: Span) -> str | None:
    raw = _attr(span, "llm.model_name") or _attr(span, "gen_ai.request.model")
    if not isinstance(raw, str):
        return raw
    # litellm prefixes the model with the provider (e.g. 'openai/claude-haiku-...').
    # The provider is routing metadata, not part of model identity — strip it.
    if "/" in raw:
        raw = raw.split("/", 1)[1]
    return raw


# ---------------------------------------------------------------------------
# Entity identification
# ---------------------------------------------------------------------------


def _identify_entities(spans: list[Span]) -> tuple[dict[str, ProtoEntity], list[str]]:
    """Returns (natural_key -> entity, notes).

    natural_key shapes:
      service:<name>             — agents and remote tool services
      external_client:<name>     — service that only emits CLIENT/INTERNAL
      external_service:<host>    — uninstrumented HTTP target host (non-LLM)
      local_tool:<svc>:<name>    — in-process tool, identified by parent service + tool name
      llm:<host>/<model>         — LLM endpoint identified by base URL host + model
    """
    notes: list[str] = []
    by_service: dict[str, list[Span]] = {}
    for s in spans:
        by_service.setdefault(_service(s), []).append(s)

    entities: dict[str, ProtoEntity] = {}

    def _add(natural_key: str, kind: str, display: str, note: str) -> ProtoEntity:
        if natural_key in entities:
            return entities[natural_key]
        e = ProtoEntity(
            id=str(uuid.uuid4()),
            kind=kind,
            natural_key=natural_key,
            display_name=display,
            detected_from=note,
        )
        entities[natural_key] = e
        return e

    # 1. Services that emit at least one SERVER span are agents or tools.
    for svc, svc_spans in by_service.items():
        kinds = {s.kind for s in svc_spans}
        if "SERVER" not in kinds:
            continue
        is_tool = any(
            s.kind == "SERVER" and (s.name or "").upper().startswith("POST /MCP")
            for s in svc_spans
        )
        natural_key = f"service:{svc}"
        if is_tool:
            _add(natural_key, "tool", svc, "service has SERVER span on /mcp path")
        else:
            note = (
                "service has OpenInference AGENT/CHAIN spans"
                if _has_oi_kind_in_service(by_service, svc, "AGENT", "CHAIN")
                else "service has SERVER spans (default to agent)"
            )
            _add(natural_key, "agent", svc, note)

    # 2. Services that only emit CLIENT/INTERNAL are external_clients.
    root_spans = [s for s in spans if s.parent_id is None]
    root_services = {_service(s) for s in root_spans}
    for svc, svc_spans in by_service.items():
        kinds = {s.kind for s in svc_spans}
        if "SERVER" in kinds:
            continue
        natural_key = f"external_client:{svc}"
        note = (
            "service has only CLIENT/INTERNAL spans and originates the trace"
            if svc in root_services
            else "service has only CLIENT/INTERNAL spans"
        )
        _add(natural_key, "external_client", svc, note)

    # 3. LLM entities — from OpenInference LLM-kind INTERNAL spans.
    #    Identify (host, model); host falls back via descendant CLIENT, then '(unknown)'.
    #    Per-model: if the same model is seen with both a known host and '(unknown)',
    #    collapse the unknown-host entity into the known one (instrumentation gap, not
    #    a different endpoint).
    llm_hosts_by_model: dict[str, set[str]] = {}
    for s in spans:
        if s.kind != "INTERNAL" or not _is_oi_kind(s, "LLM"):
            continue
        host = _llm_host_for(s, spans) or "(unknown)"
        model = _llm_model_for(s) or "(unknown)"
        llm_hosts_by_model.setdefault(model, set()).add(host)

    # Decide a canonical host per model (any concrete host beats '(unknown)').
    canonical_host_for_model: dict[str, str] = {}
    for model, hosts in llm_hosts_by_model.items():
        concrete = [h for h in hosts if h != "(unknown)"]
        canonical_host_for_model[model] = concrete[0] if concrete else "(unknown)"

    llm_hosts: set[str] = set()
    for model, host in canonical_host_for_model.items():
        natural_key = f"llm:{host}/{model}"
        display = f"{model} @ {host}" if host != "(unknown)" else model
        _add(natural_key, "llm", display, "OpenInference LLM-kind span")
        llm_hosts.add(host)

    # 4. External services: hosts referenced via CLIENT spans whose http.url host
    #    does not match any known service or LLM host. The LLM-host filter prevents
    #    the litellm gateway from being double-counted.
    known_service_names = set(by_service.keys())
    for s in spans:
        if s.kind != "CLIENT":
            continue
        host = _http_url_host(s)
        if not host:
            continue
        children = [c for c in spans if c.parent_id == s.span_id and c.trace_id == s.trace_id]
        if any(c.kind == "SERVER" for c in children):
            continue
        if host in known_service_names or host in llm_hosts:
            continue
        natural_key = f"external_service:{host}"
        _add(natural_key, "external_service", host, "uninstrumented HTTP target host on a CLIENT span")

    # 5. Local tools: OpenInference TOOL-kind INTERNAL spans, EXCLUDING agent
    #    delegation primitives (kagenti.call.kind == 'agent_consultation').
    for s in spans:
        if s.kind != "INTERNAL":
            continue
        if not _is_oi_kind(s, "TOOL"):
            continue
        if _is_agent_delegation(s):
            continue
        svc = _service(s)
        tool_name = s.name or "(unnamed tool)"
        natural_key = f"local_tool:{svc}:{tool_name}"
        _add(natural_key, "tool", tool_name, f"OpenInference TOOL span inside agent {svc}")

    notes.append(f"identified {len(entities)} entities across {len(by_service)} services")
    return entities, notes


# ---------------------------------------------------------------------------
# Payload extraction
# ---------------------------------------------------------------------------


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, default=str).encode("utf-8")


def _hash_payload(canonical: bytes) -> str:
    return hashlib.sha256(canonical).hexdigest()


def _extract_llm_messages(span: Span, prefix: str) -> list[dict[str, Any]] | None:
    attrs = span.attributes or {}
    msgs: dict[int, dict[str, Any]] = {}
    full_prefix = f"{prefix}."
    for key, value in attrs.items():
        if not key.startswith(full_prefix):
            continue
        rest = key[len(full_prefix) :]
        parts = rest.split(".", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        idx = int(parts[0])
        sub = parts[1]
        msg = msgs.setdefault(idx, {})
        msg[sub] = value
    if not msgs:
        return None
    return [msgs[i] for i in sorted(msgs)]


def _payload_for_llm_call(generation_span: Span) -> tuple[ProtoPayload | None, ProtoPayload | None]:
    request = _extract_llm_messages(generation_span, "llm.input_messages")
    response = _extract_llm_messages(generation_span, "llm.output_messages")
    req = None
    resp = None
    if request is not None:
        canon = _canonical_bytes({"messages": request})
        req = ProtoPayload(
            content_hash=_hash_payload(canon),
            content_kind="llm_chat_prompt",
            content={"messages": request},
            byte_size=len(canon),
        )
    if response is not None:
        canon = _canonical_bytes({"messages": response})
        resp = ProtoPayload(
            content_hash=_hash_payload(canon),
            content_kind="llm_completion",
            content={"messages": response},
            byte_size=len(canon),
        )
    return req, resp


def _payload_for_tool_call(tool_span: Span) -> tuple[ProtoPayload | None, ProtoPayload | None]:
    iv = _attr(tool_span, "input.value")
    ov = _attr(tool_span, "output.value")
    req = None
    resp = None
    if iv is not None:
        canon = _canonical_bytes(iv)
        req = ProtoPayload(
            content_hash=_hash_payload(canon),
            content_kind="tool_call_arguments",
            content=iv,
            byte_size=len(canon),
        )
    if ov is not None:
        canon = _canonical_bytes(ov)
        resp = ProtoPayload(
            content_hash=_hash_payload(canon),
            content_kind="tool_call_result",
            content=ov,
            byte_size=len(canon),
        )
    return req, resp


# ---------------------------------------------------------------------------
# Anchor identification & interactions
# ---------------------------------------------------------------------------


def _walk_descendants(spans: list[Span], root: Span) -> Iterator[Span]:
    """Yield the root and all its descendants in seq order."""
    by_parent: dict[str | None, list[Span]] = {}
    for s in spans:
        by_parent.setdefault(s.parent_id, []).append(s)
    stack = [root]
    while stack:
        s = stack.pop()
        yield s
        for child in by_parent.get(s.span_id, []):
            stack.append(child)


def _has_cross_service_descendant_server(
    spans: list[Span], anchor: Span, anchor_service: str
) -> bool:
    """Used to dedup local-tool/cross-service overlaps (NOTES.md problem 1)."""
    for ev in _walk_descendants(spans, anchor):
        if ev.span_id == anchor.span_id:
            continue
        if ev.kind == "SERVER" and _service(ev) != anchor_service:
            return True
    return False


def _ancestor_chain(
    by_id: dict[tuple[str, str], Span], span: Span
) -> list[Span]:
    """Walk up to the root, returning ancestors (not including span itself)."""
    out: list[Span] = []
    cur = span
    while cur.parent_id:
        parent = by_id.get((cur.trace_id, cur.parent_id))
        if parent is None:
            break
        out.append(parent)
        cur = parent
    return out


def _aggregate_error(spans_in_subtree: list[Span]) -> bool | None:
    """NOTES.md problem 4: bubble up boolean error from descendants."""
    saw_true = False
    saw_false = False
    for s in spans_in_subtree:
        if s.error is True:
            saw_true = True
        elif s.error is False:
            saw_false = True
    if saw_true:
        return True
    if saw_false:
        return False
    return None


def _extract_interactions(
    spans: list[Span],
    entities_by_key: dict[str, ProtoEntity],
) -> tuple[list[ProtoInteraction], list[ProtoInteractionSpan], list[ProtoPayload], list[str]]:
    notes: list[str] = []
    interactions: list[ProtoInteraction] = []
    interaction_spans: list[ProtoInteractionSpan] = []
    payloads: dict[str, ProtoPayload] = {}

    by_id: dict[tuple[str, str], Span] = {(s.trace_id, s.span_id): s for s in spans}

    def _entity(key: str) -> ProtoEntity | None:
        return entities_by_key.get(key)

    def _ensure_payload(p: ProtoPayload | None) -> str | None:
        if p is None:
            return None
        if p.content_hash not in payloads:
            payloads[p.content_hash] = p
        return p.content_hash

    def _attach_evidence(
        interaction_id: str,
        anchor_span_id: str,
        root: Span,
        extra: list[Span] | None = None,
    ) -> list[Span]:
        """Attach root + descendants (+ extra) as evidence; return them."""
        attached: list[Span] = []
        seen: set[str] = set()
        for ev in _walk_descendants(spans, root):
            if ev.span_id in seen:
                continue
            seen.add(ev.span_id)
            attached.append(ev)
            interaction_spans.append(
                ProtoInteractionSpan(
                    interaction_id=interaction_id,
                    trace_id=ev.trace_id,
                    span_id=ev.span_id,
                    is_anchor=(ev.span_id == anchor_span_id),
                )
            )
        for ev in extra or []:
            if ev.span_id in seen:
                continue
            seen.add(ev.span_id)
            attached.append(ev)
            interaction_spans.append(
                ProtoInteractionSpan(
                    interaction_id=interaction_id,
                    trace_id=ev.trace_id,
                    span_id=ev.span_id,
                    is_anchor=False,
                )
            )
        return attached

    # 1. Cross-service SERVER anchors. For agent_consultation delegations the
    #    nearest ancestor TOOL span is added as evidence (and its payload
    #    becomes the interaction's payload).
    seen_anchor_span_ids: set[str] = set()

    for s in spans:
        if s.kind != "SERVER":
            continue
        parent = by_id.get((s.trace_id, s.parent_id)) if s.parent_id else None
        if parent is not None and _service(parent) == _service(s):
            continue
        callee_key = f"service:{_service(s)}"
        callee = _entity(callee_key)
        if callee is None:
            continue

        ancestors = _ancestor_chain(by_id, s)
        # A delegation TOOL span belongs to the *caller's* service. Walking past
        # a service boundary would pick up a delegation from an outer agent
        # (e.g. travel-advisor's delegate→booking-agent showing up on
        # booking-agent → book_flight) which is wrong.
        caller_service = _service(parent) if parent is not None else None
        delegation_tool_span: Span | None = None
        for anc in ancestors:
            if caller_service is not None and _service(anc) != caller_service:
                break
            if (
                anc.kind == "INTERNAL"
                and _is_oi_kind(anc, "TOOL")
                and _is_agent_delegation(anc)
            ):
                delegation_tool_span = anc
                break

        if parent is None:
            ext_clients = [e for e in entities_by_key.values() if e.kind == "external_client"]
            caller = ext_clients[0] if ext_clients else None
            caller_note = "no parent span — caller is the trace's external_client"
        else:
            caller_key = f"service:{_service(parent)}"
            caller = _entity(caller_key) or _entity(f"external_client:{_service(parent)}")
            caller_note = f"parent {parent.kind} span on {_service(parent)}"
        if caller is None:
            notes.append(f"SKIP anchor {s.span_id}: cannot resolve caller ({caller_note})")
            continue

        ix_id = str(uuid.uuid4())
        extra_evidence: list[Span] = []
        req_hash = None
        resp_hash = None
        summary_extra = ""
        if delegation_tool_span is not None:
            extra_evidence.append(delegation_tool_span)
            # Also attach intermediate ancestors between TOOL and SERVER (typically a CLIENT POST).
            for anc in ancestors:
                if anc.span_id == delegation_tool_span.span_id:
                    break
                extra_evidence.append(anc)
            req_p, resp_p = _payload_for_tool_call(delegation_tool_span)
            req_hash = _ensure_payload(req_p)
            resp_hash = _ensure_payload(resp_p)
            target = _attr(delegation_tool_span, "gen_ai.agent.name")
            if target:
                summary_extra = f" [delegate→{target}]"

        attached = _attach_evidence(ix_id, s.span_id, s, extra_evidence)

        interactions.append(
            ProtoInteraction(
                id=ix_id,
                caller_entity_id=caller.id,
                callee_entity_id=callee.id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                error=_aggregate_error(attached),
                request_payload_hash=req_hash,
                response_payload_hash=resp_hash,
                summary=f"{caller.display_name} → {callee.display_name} ({s.name}){summary_extra}",
            )
        )
        seen_anchor_span_ids.add(s.span_id)

    # 2. LLM calls — anchored on OpenInference LLM-kind INTERNAL spans.
    for s in spans:
        if s.kind != "INTERNAL" or not _is_oi_kind(s, "LLM"):
            continue
        if s.span_id in seen_anchor_span_ids:
            continue
        caller_key = f"service:{_service(s)}"
        caller = _entity(caller_key)
        if caller is None:
            continue
        model = _llm_model_for(s) or "(unknown)"
        # The entity index keys by f"llm:{host}/{model}" but the host on this
        # span may be '(unknown)' while the canonical entity uses a real host.
        # Look up by suffix match on /model.
        callee = next(
            (e for e in entities_by_key.values()
             if e.kind == "llm" and e.natural_key.endswith(f"/{model}")),
            None,
        )
        if callee is None:
            notes.append(f"LLM span {s.span_id} on {_service(s)}: no llm entity for model {model}")
            continue
        req, resp = _payload_for_llm_call(s)
        ix_id = str(uuid.uuid4())
        attached = _attach_evidence(ix_id, s.span_id, s)
        interactions.append(
            ProtoInteraction(
                id=ix_id,
                caller_entity_id=caller.id,
                callee_entity_id=callee.id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                error=_aggregate_error(attached),
                request_payload_hash=_ensure_payload(req),
                response_payload_hash=_ensure_payload(resp),
                summary=f"{caller.display_name} → {callee.display_name}",
            )
        )
        seen_anchor_span_ids.add(s.span_id)

    # 3. Local tool calls — anchored on OpenInference TOOL-kind INTERNAL spans.
    #    Skips agent-delegation spans (handled as evidence in pass 1) and any
    #    TOOL span whose subtree contains a cross-service SERVER (NOTES.md problem 1).
    for s in spans:
        if s.kind != "INTERNAL" or not _is_oi_kind(s, "TOOL"):
            continue
        if _is_agent_delegation(s):
            continue
        if s.span_id in seen_anchor_span_ids:
            continue
        if _has_cross_service_descendant_server(spans, s, _service(s)):
            # Real cross-service tool (e.g. MCP-deployed) — already covered by the SERVER anchor.
            notes.append(
                f"TOOL span {s.span_id} ({s.name}) suppressed: subtree contains cross-service SERVER"
            )
            continue
        caller_key = f"service:{_service(s)}"
        caller = _entity(caller_key)
        if caller is None:
            continue
        callee_key = f"local_tool:{_service(s)}:{s.name}"
        callee = _entity(callee_key)
        if callee is None:
            continue
        req, resp = _payload_for_tool_call(s)
        ix_id = str(uuid.uuid4())
        attached = _attach_evidence(ix_id, s.span_id, s)
        interactions.append(
            ProtoInteraction(
                id=ix_id,
                caller_entity_id=caller.id,
                callee_entity_id=callee.id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                error=_aggregate_error(attached),
                request_payload_hash=_ensure_payload(req),
                response_payload_hash=_ensure_payload(resp),
                summary=f"{caller.display_name} → {callee.display_name} (tool)",
            )
        )
        seen_anchor_span_ids.add(s.span_id)

    notes.append(
        f"produced {len(interactions)} interactions, "
        f"{len(interaction_spans)} evidence rows, {len(payloads)} unique payloads"
    )
    return interactions, interaction_spans, list(payloads.values()), notes


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def extract(spans: Iterable[Span]) -> ExtractResult:
    spans_list = list(spans)
    spans_list.sort(key=lambda s: (s.started_at, s.seq))

    entities_by_key, ent_notes = _identify_entities(spans_list)
    interactions, ix_spans, payloads, ix_notes = _extract_interactions(
        spans_list, entities_by_key
    )

    return ExtractResult(
        entities=list(entities_by_key.values()),
        interactions=interactions,
        interaction_spans=ix_spans,
        payloads=payloads,
        notes=ent_notes + ix_notes,
    )
