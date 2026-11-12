"""Retrieval submodule — what a held exchange's request carried, for the verdict service.

Part of the :mod:`data_governance.retrieval` package (see its ``__init__`` for the
seam split). A Sentry gate holds an outbound request and asks the verdict service
(:mod:`data_governance.risk.api.verdict_routes`) about that one exchange: its trace id
and request span id (``lineage.exchange.id``), whose interaction id is
``uuid5(trace_id/exchange_id)``. The answering record comes from
:func:`.risk.get_request_risk`; these two reads give the rest, batched for every
exchange a process waits on:

- :func:`get_exchange_requests` — per exchange, its request leg's payload state (for
  priority classification) and whether the request body was read whole, derived from
  the request span's facts (wire contract §5): ``http.request.body.size`` is always
  there, and a size above 0 beside no ``input.value`` is a body the sidecar saw and
  did not emit (``body_not_captured``); a value cut at ``max_payload_bytes`` ends
  ``…[truncated]`` (``body_truncated``). No value and no size (a sidecar older than
  contract v1.8) counts as not captured. A parser's reduction (a2a, mcp, inference)
  carries no such fact, so it reads as whole: this reads what the sidecar reports, no
  further.
- :func:`get_request_findings` — for the given interactions, the request payload's
  **Classification** findings and, for the ids asked, its stored content when it is no
  larger than ``max_content_bytes``; an interaction whose request payload is not
  classified yet has no entry.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from data_governance import db

__all__ = [
    "ExchangeRequestView",
    "RequestFindingsView",
    "get_exchange_requests",
    "get_request_findings",
]

#: Wire contract §5's mark on a value cut at ``max_payload_bytes``.
_TRUNCATED_SUFFIX = "…[truncated]"


@dataclass(frozen=True)
class ExchangeRequestView:
    """One waited exchange's request, as derived from its leg and its span."""

    interaction_id: str
    has_request_leg: bool
    payload_hash: str | None
    payload_stored: bool
    classified: bool
    #: Why a record cannot have judged the whole request body (``body_not_captured``,
    #: ``body_truncated``), or None when it was read whole or there is no request leg yet.
    unread_body: str | None


@dataclass(frozen=True)
class RequestFindingsView:
    """One interaction's request payload: its findings, and its content when asked for."""

    interaction_id: str
    findings: Any
    content: Any | None


# One row per exchange. The span is the exchange's own: its span id is the exchange id.
_REQUESTS_SQL = """
SELECT w.id, l.interaction_id IS NOT NULL, l.payload_hash,
       p.content_hash IS NOT NULL, pc.content_hash IS NOT NULL,
       s.attributes->>'http.request.body.size',
       right(s.attributes->>'input.value', %s) = %s
  FROM unnest(%s::text[], %s::text[], %s::text[]) AS w(id, trace_id, exchange_id)
  LEFT JOIN interaction_legs l ON l.interaction_id = w.id AND l.leg_type = 'request'
  LEFT JOIN interaction_payloads p ON p.content_hash = l.payload_hash
  LEFT JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
  LEFT JOIN spans s ON s.trace_id = w.trace_id AND s.span_id = w.exchange_id
"""

_FINDINGS_SQL = """
SELECT w.id, pc.findings, CASE WHEN w.with_content AND p.byte_size <= %s THEN p.content END
  FROM unnest(%s::text[], %s::boolean[]) AS w(id, with_content)
  JOIN interaction_legs l ON l.interaction_id = w.id AND l.leg_type = 'request'
  JOIN interaction_payloads p ON p.content_hash = l.payload_hash
  JOIN payload_classifications pc ON pc.content_hash = l.payload_hash
"""


def _unread_body(has_leg: bool, payload_hash: str | None, body_size: str | None, truncated: bool | None) -> str | None:
    if not has_leg:
        return None
    if truncated:
        return "body_truncated"
    if payload_hash is None and body_size != "0":
        return "body_not_captured"
    return None


def get_exchange_requests(exchanges: Sequence[tuple[str, str, str]]) -> dict[str, ExchangeRequestView]:
    """Every exchange's request, keyed by interaction id; *exchanges* are
    ``(interaction id, trace id, exchange id)``. One read for all of them; an exchange
    with no request leg yet has ``has_request_leg`` false and no ``unread_body``."""
    if not exchanges:
        return {}
    ids, traces, exchange_ids = (list(column) for column in zip(*exchanges, strict=True))
    with db.transaction() as tx:
        rows = tx.fetch_all(_REQUESTS_SQL, (len(_TRUNCATED_SUFFIX), _TRUNCATED_SUFFIX, ids, traces, exchange_ids))
    return {
        iid: ExchangeRequestView(iid, bool(has_leg), payload_hash, bool(stored), bool(classified),
                                 _unread_body(has_leg, payload_hash, body_size, truncated))
        for iid, has_leg, payload_hash, stored, classified, body_size, truncated in rows
    }


def get_request_findings(
    interaction_ids: Sequence[str], *, content_for: Collection[str] = (), max_content_bytes: int = 0,
) -> dict[str, RequestFindingsView]:
    """The request payload's findings for each of *interaction_ids* whose payload is
    classified, and its content for the ids in *content_for* when it is stored at no more
    than *max_content_bytes* (None otherwise). One read for all of them."""
    if not interaction_ids:
        return {}
    ids = list(interaction_ids)
    with db.transaction() as tx:
        rows = tx.fetch_all(_FINDINGS_SQL, (max_content_bytes, ids, [iid in content_for for iid in ids]))
    return {iid: RequestFindingsView(iid, findings, content) for iid, findings, content in rows}
