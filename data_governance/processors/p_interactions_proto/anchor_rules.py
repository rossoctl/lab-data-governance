"""Five structural anchor rules per ADR-0009.

THROWAWAY prototype. Pure functions; no DB I/O. Each rule decides whether
a span anchors an interaction based on span structure ONLY (kind, parent,
attribute markers). Entity-kind resolution happens in caller_inference.py.

| rule              | trigger                                                              |
|-------------------|----------------------------------------------------------------------|
| cross-service     | Sn has parent Sp on a different canonical service                    |
| orphan-server     | SERVER Sn has no in-trace parent                                     |
| openinference-llm | Sn has openinference.span.kind = LLM                                 |
| openinference-tool| Sn has oi.span.kind = TOOL and kagenti.call.kind != agent_consultation|
| external-http     | CLIENT Sn whose host is not a known entity, no in-trace SERVER child |
"""

from __future__ import annotations

import dataclasses
from typing import Any

from data_governance.retrieval import Span

from .caller_inference import canonical_service_name


# ---------------------------------------------------------------------------
# Anchor rule output
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AnchorDecision:
    rule: str  # cross-service | orphan-server | openinference-llm | openinference-tool | external-http
    anchor_span_ids: tuple[str, ...]  # one or two span_ids that anchor the interaction
    # "primary" anchor — the one whose parent chain is walked to place the
    # interaction in the interaction tree (ADR-0008). Always one of anchor_span_ids.
    primary_anchor_span_id: str
    notes: str = ""


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _is_oi_kind(span: Span, *kinds: str) -> bool:
    return _attr(span, "openinference.span.kind") in kinds


# ---------------------------------------------------------------------------
# The five rules
# ---------------------------------------------------------------------------


def cross_service(
    span: Span, parent: Span | None
) -> AnchorDecision | None:
    """Fires on the *child* span Sn whose parent Sp is on a different
    canonical service. Both Sp and Sn anchor the resulting interaction.

    The primary anchor is Sn (the child) — its parent chain reaches Sp,
    so walking up from Sn picks up the enclosing interaction correctly.
    """
    if parent is None:
        return None
    sp_canon = canonical_service_name(parent)
    sn_canon = canonical_service_name(span)
    if sp_canon is None or sn_canon is None:
        return None
    if sp_canon == sn_canon:
        return None
    return AnchorDecision(
        rule="cross-service",
        anchor_span_ids=(parent.span_id, span.span_id),
        primary_anchor_span_id=span.span_id,
        notes=f"{sp_canon} -> {sn_canon}",
    )


def orphan_server(
    span: Span, parent: Span | None, parent_id_known_unresolvable: bool = False
) -> AnchorDecision | None:
    """Fires on a SERVER span whose parent_id is NULL (root SERVER), or whose
    parent_id is set but resolves to no in-trace span AND we've decided that
    parent will not arrive (caller passes parent_id_known_unresolvable=True).

    The procedure module passes True only at batch-end / on a defer timeout —
    not eagerly for every SERVER whose parent simply hasn't arrived yet. That
    avoids speculatively creating provisional `client` entities for every
    intra-service SERVER whose CLIENT parent is moments behind in seq order.
    """
    if span.kind != "SERVER":
        return None
    if span.parent_id is None:
        return AnchorDecision(
            rule="orphan-server",
            anchor_span_ids=(span.span_id,),
            primary_anchor_span_id=span.span_id,
            notes="root SERVER (no parent_id)",
        )
    if parent is None and parent_id_known_unresolvable:
        return AnchorDecision(
            rule="orphan-server",
            anchor_span_ids=(span.span_id,),
            primary_anchor_span_id=span.span_id,
            notes="orphan SERVER (parent_id refers to span not in trace)",
        )
    return None


def openinference_llm(span: Span) -> AnchorDecision | None:
    """OpenInference LLM-kind span. Caller is decided by the surrounding
    service; callee is the llm entity."""
    if not _is_oi_kind(span, "LLM"):
        return None
    return AnchorDecision(
        rule="openinference-llm",
        anchor_span_ids=(span.span_id,),
        primary_anchor_span_id=span.span_id,
    )


def openinference_tool(span: Span) -> AnchorDecision | None:
    """OpenInference TOOL-kind span. Per ADR-0010, this rule fires
    *independently* of the cross-service rule — deployed-MCP tools produce
    both interactions, connected by the tree. Per ADR-0010 'symmetry'
    principle, this rule fires for delegate FunctionTools as well: the
    `agent → tool:<...>:delegate_to_*` interaction captures the agent's
    intent layer, while the underlying cross-service A2A interaction
    captures the transport layer.
    """
    if not _is_oi_kind(span, "TOOL"):
        return None
    return AnchorDecision(
        rule="openinference-tool",
        anchor_span_ids=(span.span_id,),
        primary_anchor_span_id=span.span_id,
    )


def external_http(
    span: Span,
    in_trace_children: list[Span],
    known_service_canonicals: set[str],
) -> AnchorDecision | None:
    """CLIENT span whose target host is not a known entity *and* has no
    in-trace SERVER child (i.e. the callee is uninstrumented).

    `known_service_canonicals` is the set of canonical service names already
    discovered in this trace — used as a cheap "is this hostname one of our
    own services?" check. A future v2 rule consults a registered-service
    inventory; the prototype uses what we've seen so far.
    """
    if span.kind != "CLIENT":
        return None
    has_server_child = any(c.kind == "SERVER" for c in in_trace_children)
    if has_server_child:
        return None
    # Best-effort host extraction
    from .caller_inference import _http_host  # local import to keep module top tidy

    host = _http_host(span)
    if not host:
        return None
    if host in known_service_canonicals:
        return None
    return AnchorDecision(
        rule="external-http",
        anchor_span_ids=(span.span_id,),
        primary_anchor_span_id=span.span_id,
        notes=f"host={host}",
    )


# ---------------------------------------------------------------------------
# Aggregator: fire all rules on a span; return all matches.
# ---------------------------------------------------------------------------


def fire_all(
    span: Span,
    parent: Span | None,
    in_trace_children: list[Span],
    known_service_canonicals: set[str],
    parent_id_known_unresolvable: bool = False,
) -> list[AnchorDecision]:
    """Per ADR-0010, multiple rules can fire on the same span (e.g. the
    cross-service CLIENT POST under an openinference-tool ancestor). Return
    all matches; the procedure module decides what interactions to create."""
    out: list[AnchorDecision] = []
    d = cross_service(span, parent)
    if d:
        out.append(d)
    d = orphan_server(span, parent, parent_id_known_unresolvable)
    if d:
        out.append(d)
    d = openinference_llm(span)
    if d:
        out.append(d)
    d = openinference_tool(span)
    if d:
        out.append(d)
    d = external_http(span, in_trace_children, known_service_canonicals)
    if d:
        out.append(d)
    return out
