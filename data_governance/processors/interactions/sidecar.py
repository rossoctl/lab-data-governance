"""Trace-level derivation from two-span AuthBridge sidecar lineage — the
``sidecar`` interactions algorithm (ADR-0030).

One HTTP exchange through the sidecar emits TWO spans (request + response),
joined by ``lineage.exchange.id`` (= the request span's own span id). This
module turns a trace's worth of those spans into the derived interaction graph:
the genuine Case-Y source ADR-0025 anticipated, whose request and response are
distinct ``(trace_id, span_id)`` rows attached to distinct legs.

Classification is the shared facts table in :mod:`data_governance.sidecar_facts`
— pure ``(direction, protocol[, mcp.method])``, never a body: a bodyless
exchange (unparsed protocol, ``capture_io`` off, streamed response) still
derives a complete, first-class interaction with NULL payload hashes (sidecar
wire contract, "Interactions are independent of payloads").

``derive_trace`` is a pure reconcile over ALL spans of one trace: idempotent,
and any arrival order (echo before parent, response before request, fully
shuffled) converges to the same tables. Because the reconcile is
whole-trace-authoritative — an inbound request is only an anchor until an
outbound ancestor arrives, so the wanted set can *shrink* — it owns its own
write path with trace-scoped deletes rather than sharing :func:`state.flush`,
whose anchors are emit-once and never deleted (ADR-0030 records the trade).
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

from data_governance import db
from data_governance.retrieval import Span
from data_governance.retrieval.spans import _COLUMNS, _row_to_span
from data_governance.sidecar_facts import Kinds, classify_attrs

from .procedure import (
    _canonical_bytes,
    _entity_id,
    _hash_payload,
    _interaction_id,
)

_SELECT_COLS = ", ".join(_COLUMNS)

_UNKNOWN = "(unknown)"

# The only shape a Kubernetes namespace can have (RFC 1123 label) — the producer
# refuses to start on any other, so a present value that fails this is a
# contract violation, not data.
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def _ident_key(namespace: str | None, ident: str) -> str:
    """The identity half of a natural key: ``namespace/ident`` for a pod,
    ``ident`` for everything without a namespace. One place, because the
    trace-local maps (``_self_kinds``) and the persisted key must agree."""
    return f"{namespace}/{ident}" if namespace else ident


def classify(req_span: Span) -> Kinds:
    """Static (direction, protocol[, mcp.method]) -> kinds. Facts only.

    The table lives in :mod:`data_governance.sidecar_facts` (shared with the
    read-time re-derivation in retrieval). Assumes *req_span* is a request span
    (``lineage.role == request``); callers pass only request spans.
    """
    return classify_attrs(req_span.attributes)


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _protocol(span: Span) -> str:
    """The exchange's protocol as the kind table spells it (unknown → http),
    the same normalization ``classify_attrs`` applies."""
    proto = str(_attr(span, "lineage.protocol") or "http").lower()
    return proto if proto in ("a2a", "mcp", "inference") else "http"


def _direction(span: Span) -> str:
    return str(_attr(span, "lineage.direction") or "").lower()


def _role(span: Span) -> str:
    return str(_attr(span, "lineage.role") or "").lower()


def _exchange_id(span: Span) -> str | None:
    xid = _attr(span, "lineage.exchange.id")
    return str(xid) if xid else None


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _require_self_id(span: Span) -> str:
    """``lineage.self.id`` is contract-unconditional ("on: both", no caveat) and
    the producer refuses to start without a resolved identity. A span missing it
    is a producer contract violation; minting a shared ``agent:(unknown)``
    entity here would silently weld every broken pod into one graph participant
    — fail loudly instead ("no mechanism may guess")."""
    self_id = _attr(span, "lineage.self.id")
    if not self_id:
        raise ValueError(
            f"span {span.span_id}: lineage span without lineage.self.id "
            "(contract-unconditional) — producer contract violation"
        )
    return str(self_id)


def _self_namespace(span: Span) -> str | None:
    """``lineage.self.namespace`` (contract v1.7, on both spans): the pod's
    Kubernetes namespace, the other half of its identity — ``self.id`` alone
    welds a same-named workload in two namespaces onto one entity. A v1.7
    producer refuses to start without one, so on its spans the fact is always
    present. It is absent only on spans a pre-v1.7 producer emitted, which
    are stored and replayable: those key the entity the way v1.6 did, without
    a namespace. That is honest absence, not a guess — the consumer never
    infers a namespace from ``peer.host``, a SPIFFE path, or anything else.

    Present but not a DNS label — empty, padded, or any other shape — is not
    absence: the producer refuses to start on such a value, so one on the
    wire is a contract violation, and folding it into "absent" would re-weld
    the pod onto the un-namespaced row. Die loudly instead, as a missing
    ``self.id`` does ("no mechanism may guess")."""
    ns = _attr(span, "lineage.self.namespace")
    if ns is None:
        return None
    if not isinstance(ns, str) or not _DNS_LABEL.match(ns):
        raise ValueError(
            f"span {span.span_id}: lineage.self.namespace={ns!r} is not a DNS "
            "label — producer contract violation (v1.7 refuses to start on one)"
        )
    return ns


def _self_entity(kind: str, span: Span) -> _Entity:
    """The entity this span's own pod is: ``self.id`` under ``self.namespace``."""
    return _Entity(kind, _require_self_id(span), _self_namespace(span))


def _self_key(span: Span) -> str:
    """One string per pod for the trace-local maps (``_self_kinds``): the same
    ``namespace/id`` composition the natural key uses, so two same-named pods
    in different namespaces never share a verdict."""
    return _ident_key(_self_namespace(span), _require_self_id(span))


# ---------------------------------------------------------------------------
# Row construction (pure)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Payload:
    content_hash: str
    content_kind: str
    content: Any
    byte_size: int


@dataclasses.dataclass
class _Entity:
    """One graph participant. ``namespace`` is set only for entities that ARE
    a pod (identified by ``lineage.self.id``); a user, an anonymous client, an
    LLM endpoint, or an un-sidecared callee named by ``peer.host`` has none.

    ``natural_key`` is the identity: the ``entities`` UNIQUE column and the
    input to ``_entity_id`` (uuid5), so the namespace sits INSIDE it —
    ``agent:team2/weather-service`` — and not only in the ``namespace``
    column, which alone could never split two rows (migration 0020)."""

    kind: str
    ident: str
    namespace: str | None = None

    @property
    def ident_key(self) -> str:
        return _ident_key(self.namespace, self.ident)

    @property
    def natural_key(self) -> str:
        return f"{self.kind}:{self.ident_key}"


@dataclasses.dataclass
class _Row:
    """One interaction's fully-computed facts (pure; no DB writes yet).

    ``response_span_id`` / ``resp_seq`` / ``ended_at`` / ``response_payload``
    are ``None`` while the exchange is in flight — the response leg is then
    simply absent (never fabricated); ``error`` is the exchange tri-state
    (None = in flight)."""

    interaction_id: str
    trace_id: str
    anchor_span_id: str
    parent_anchor_span_id: str | None
    caller: _Entity
    callee: _Entity
    started_at: Any
    ended_at: Any
    error: bool | None
    request_payload: _Payload | None
    response_payload: _Payload | None
    seq: int
    response_span_id: str | None
    resp_seq: int | None


def _mk_payload(content_kind: str | None, value: Any) -> _Payload | None:
    """Content-address one body. None when the body is absent OR the protocol
    carries no semantic content kind — the row stays complete with a NULL hash.

    Absence is judged on the RAW attribute, before ``_coerce``: a wire body
    that decodes to JSON ``null`` still produces a payload row (content null,
    hash of ``b"null"``) — a captured body must stay distinguishable from
    ``capture_io`` being off."""
    if content_kind is None or value is None:
        return None
    content = _coerce(value)
    canon = _canonical_bytes(content)
    return _Payload(_hash_payload(canon), content_kind, content, len(canon))


def _outcome_error(resp: Span | None) -> bool | None:
    """Map the response span's ``lineage.outcome`` to the interaction error flag.
    No response yet → None (in-flight). ok → False; denied/error/abandoned →
    True (an abandoned exchange is a failed row, not a dangling one)."""
    if resp is None:
        return None
    outcome = str(_attr(resp, "lineage.outcome") or "").lower()
    if outcome == "ok":
        return False
    if outcome in ("denied", "error", "abandoned"):
        return True
    # No outcome attribute (e.g. an unparsed response): fall back to the span's
    # own OTEL error projection. When that too is absent, the honest answer is
    # None — "we could not determine the outcome" must never be recorded as
    # "it succeeded" on a governance column that can hold the unknown.
    return bool(resp.error) if resp.error is not None else None


def _callee(kinds: Kinds, req: Span, echo: Span | None) -> _Entity:
    """Callee identity from facts. LLM: {peer.host}/{inference.model}. Inbound
    entry: this pod's (namespace, self.id). Outbound: the callee pod's own
    (namespace, self.id) read off its echo span when the callee-side inbound
    exists, else peer.host — which carries the namespace only when the caller
    used an FQDN, and the consumer does not resolve a short host into one."""
    if kinds.callee_kind == "llm":
        # peer.host is contract-conditional ("when present") and inference.model
        # comes from a parsed body that may be absent — (unknown) is the honest
        # value for both, mirroring the sanctioned client:(unknown) case.
        host = str(_attr(req, "lineage.peer.host") or _UNKNOWN)
        model = str(_attr(req, "inference.model") or _UNKNOWN)
        return _Entity("llm", f"{host}/{model}")
    if _direction(req) == "inbound":
        return _self_entity(kinds.callee_kind, req)
    if echo is not None:
        return _self_entity(kinds.callee_kind, echo)
    return _Entity(kinds.callee_kind, str(_attr(req, "lineage.peer.host") or _UNKNOWN))


def _caller(kinds: Kinds, req: Span, self_kind_of: dict[str, str]) -> _Entity:
    """Caller identity from facts. Inbound: the authenticated OAuth client when
    present, otherwise client:(unknown).  The human subject remains available
    on the evidence span but does not name the transport caller. Outbound: this
    pod's self.id, of the kind this trace
    already knows the pod to be (``_self_kinds``), else the table's default."""
    if _direction(req) == "inbound":
        client = _attr(req, "lineage.principal.client")
        if client:
            return _Entity("client", str(client))
        return _Entity("client", _UNKNOWN)
    return _self_entity(self_kind_of.get(_self_key(req), kinds.caller_kind), req)


def _self_kinds(reqs: dict[str, Span]) -> dict[str, str]:
    """What each pod (``namespace/self.id``) in this trace IS. One verdict per
    pod, from the pod's own traffic, applied where that pod is the *caller* of an outbound
    exchange (``_caller``). The callee side keeps the kind table's protocol kind:
    an echoing callee served that same protocol, so the two agree for a2a, mcp
    and inference — but not for plain http, where the outbound row says
    ``service`` and the served role says ``agent`` (a known limit, not fixed
    here). The tiers:

    1. **served role** — the kind table types the callee of an inbound exchange
       (mcp → tool, a2a → agent, inference → llm), and that callee is the pod
       itself. A pod whose inbounds disagree (it serves both a2a and mcp) is an
       agent, the table's own default.
    2. **sent protocol**, only when nothing was served in this trace — a pod
       that sends a2a or mcp behaves like an agent and is one.
    3. otherwise the pod is undecided. ``entity_kind`` has no value for that
       (migration 0004 is the fixed interface), so it falls through to the
       table's outbound default, ``agent``, until the vocabulary grows.

    The table's outbound rows say the caller is an ``agent`` because agents were
    the only pods that called out when it was written. A tool that makes its own
    egress — a weather tool fetching a forecast over http, a booking tool
    delegating over a2a — would otherwise be minted twice, ``tool:X`` for what
    it serves and ``agent:X`` for what it calls (seen live 2026-09-01). Read off
    the whole trace, so any arrival order gives the same plan; the stored
    ``entities`` row minted under a partial trace is not withdrawn (global,
    upsert-only — ADR-0030), only re-pointed away from.
    """
    served: dict[str, set[str]] = {}
    sent: dict[str, set[str]] = {}
    for s in reqs.values():
        if not _attr(s, "lineage.self.id"):
            continue
        key = _self_key(s)
        if _direction(s) == "inbound":
            served.setdefault(key, set()).add(classify(s).callee_kind)
        else:
            sent.setdefault(key, set()).add(_protocol(s))
    verdict: dict[str, str] = {}
    for pod, kinds in served.items():
        verdict[pod] = next(iter(kinds)) if len(kinds) == 1 else "agent"
    for pod, protocols in sent.items():
        if pod not in verdict and protocols & {"a2a", "mcp"}:
            verdict[pod] = "agent"
    return verdict


# ---------------------------------------------------------------------------
# Trace reconcile
# ---------------------------------------------------------------------------


def _fetch_trace_spans(tx: db.Transaction, trace_id: str) -> list[Span]:
    rows = tx.fetch_all(
        f"SELECT {_SELECT_COLS} FROM spans WHERE trace_id = %s ORDER BY seq ASC",
        (trace_id,),
    )
    return [_row_to_span(r, in_time_window=True) for r in rows]


@dataclasses.dataclass
class _Plan:
    """The pure result of reconciling a trace's spans — no DB writes yet, so it
    is directly unit-testable. ``want`` maps interaction id -> row; ``connectors``
    are (owner_anchor_span_id, span_id) pairs for every non-anchor span that
    attaches to an interaction."""

    trace_id: str
    all_spans: list[Span]
    anchor_ids: set[str]
    want: dict[str, _Row]
    connectors: list[tuple[str, str]]


def derive_trace(tx: db.Transaction, trace_id: str) -> None:
    """Reconcile one trace's interaction graph from ALL its spans. Idempotent;
    order-independent. Deletes this trace's derived rows no longer justified by
    the current span set, then retires only globally unreferenced provisional
    host:port agent/tool entities."""
    _write(tx, plan_trace(trace_id, _fetch_trace_spans(tx, trace_id)))


def plan_trace(trace_id: str, all_spans: list[Span]) -> _Plan:
    """Pure reconcile: derive anchors, interaction rows, and connector ownership
    from a trace's spans, with no DB access. Any arrival order of the same span
    set produces an equal plan."""
    parent_of: dict[str, str | None] = {s.span_id: s.parent_id for s in all_spans}

    reqs: dict[str, Span] = {}   # exchange.id (= request span id) -> request span
    resps: dict[str, Span] = {}  # exchange.id -> response span
    for s in all_spans:
        xid = _exchange_id(s)
        if not xid:
            continue  # not a sidecar lineage span
        role = _role(s)
        if role == "request":
            # A request span with a garbled direction would silently fall out
            # of BOTH anchor sets (neither outbound anchor nor inbound entry
            # nor echo) — the exchange would simply not exist in the derived
            # graph. The contract promises every exchange derives; a producer
            # violation dies loudly here instead ("no mechanism may guess").
            if _direction(s) not in ("inbound", "outbound"):
                raise ValueError(
                    f"span {s.span_id} (trace {trace_id}): lineage request span "
                    f"with lineage.direction={_direction(s)!r}, want inbound|outbound"
                )
            # The contract fixes the exchange id AS the request span's own id,
            # and everything downstream leans on that identity: it keys
            # parent_of, anchors the ancestor walk, and lands in
            # interaction_spans.span_id. A producer that drifted here would not
            # fail — it would silently derive a graph anchored on span ids that
            # do not exist. Die loudly instead ("no mechanism may guess").
            if xid != s.span_id:
                raise ValueError(
                    f"span {s.span_id} (trace {trace_id}): lineage request span "
                    f"with lineage.exchange.id={xid!r} != its own span_id — the "
                    f"contract fixes these as identical"
                )
            reqs[xid] = s
        elif role == "response":
            resps[xid] = s
        else:
            # Carries an exchange id — contractually a sidecar lineage span —
            # but a role we don't know. Dropping it silently would hide a
            # producer contract change; fail loudly instead.
            raise ValueError(
                f"span {s.span_id} (trace {trace_id}): lineage span with "
                f"lineage.role={role!r}, want request|response"
            )

    # Anchors: every outbound request, plus an inbound request with no ANCHOR
    # ancestor (the trace entry). Outbound requests are unconditionally anchors,
    # so an inbound has an anchor ancestor iff its parent chain (walked through
    # any stored non-anchor span — response/echo/bridge — with a visited guard)
    # reaches an outbound request. That is bridge-span agnostic: fake shim spans
    # sit in the chain as non-anchors and are simply walked over.
    outbound_ids = {sid for sid, s in reqs.items() if _direction(s) == "outbound"}

    def _reaches(span_id: str, targets: set[str]) -> str | None:
        seen: set[str] = set()
        cur = parent_of.get(span_id)
        while cur is not None and cur not in seen:
            seen.add(cur)
            if cur in targets:
                return cur
            cur = parent_of.get(cur)
        return None

    anchor_ids = set(outbound_ids)
    for sid, s in reqs.items():
        if _direction(s) == "inbound" and _reaches(sid, outbound_ids) is None:
            anchor_ids.add(sid)

    def _nearest_anchor(span_id: str) -> str | None:
        return _reaches(span_id, anchor_ids)

    # An outbound anchor's callee identity may come from the callee-side inbound
    # "echo" — the inbound request whose nearest anchor is this outbound (the
    # caller sidecar's tracestate stamp parents the echo under the outbound
    # request span; wire contract v1.5).
    echo_of: dict[str, Span] = {}
    for sid, s in reqs.items():
        if sid in anchor_ids or _direction(s) != "inbound":
            continue
        owner = _nearest_anchor(sid)
        if owner in outbound_ids and owner not in echo_of and _attr(s, "lineage.self.id"):
            echo_of[owner] = s

    self_kind_of = _self_kinds(reqs)

    want: dict[str, _Row] = {}
    for aid in anchor_ids:
        req = reqs[aid]
        resp = resps.get(aid)
        kinds = classify(req)
        req_pl = _mk_payload(kinds.req_content_kind, _attr(req, "input.value"))
        resp_pl = (
            _mk_payload(kinds.resp_content_kind, _attr(resp, "output.value"))
            if resp is not None
            else None
        )
        parent_anchor = _nearest_anchor(aid)
        want[_interaction_id(trace_id, aid)] = _Row(
            interaction_id=_interaction_id(trace_id, aid),
            trace_id=trace_id,
            anchor_span_id=aid,
            parent_anchor_span_id=parent_anchor,
            caller=_caller(kinds, req, self_kind_of),
            callee=_callee(kinds, req, echo_of.get(aid)),
            started_at=req.started_at,
            ended_at=(resp.ended_at or resp.started_at) if resp is not None else None,
            error=_outcome_error(resp),
            request_payload=req_pl,
            response_payload=resp_pl,
            seq=req.seq,
            response_span_id=resp.span_id if resp is not None else None,
            resp_seq=resp.seq if resp is not None else None,
        )

    connectors: list[tuple[str, str]] = []
    for s in all_spans:
        if s.span_id in anchor_ids:
            continue
        owner = _nearest_anchor(s.span_id)
        if owner is not None:
            connectors.append((owner, s.span_id))

    return _Plan(trace_id, all_spans, anchor_ids, want, connectors)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _upsert_entity(tx: db.Transaction, ent: _Entity, seq: int) -> str:
    eid = _entity_id(ent.natural_key)
    tx.execute(
        "INSERT INTO entities (id, kind, natural_key, display_name, project_name, "
        "namespace, detected_from, seq, original_seq) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        # seq is deliberately NOT in the update list: this algorithm re-derives
        # the whole trace on every arriving span, and the entity_ready stream
        # (ADR-0027) reads ``entities WHERE seq > cursor``. Taking the new seq
        # (EXCLUDED.seq) would rewrite every entity to the newest span's seq on
        # each drain, so a delivered entity kept reappearing ahead of the
        # cursor; lowering it (LEAST) could instead drop a not-yet-read entity
        # BELOW the cursor. Leaving seq exactly as inserted keeps first
        # detection monotone and delivered exactly once.
        "ON CONFLICT (natural_key) DO UPDATE SET display_name = EXCLUDED.display_name, "
        "namespace = EXCLUDED.namespace, detected_from = EXCLUDED.detected_from",
        (eid, ent.kind, ent.natural_key, ent.ident, None, ent.namespace,
         "sidecar lineage span", seq, seq),
    )
    return eid


def _upsert_payload(tx: db.Transaction, pl: _Payload | None) -> str | None:
    if pl is None:
        return None
    tx.execute(
        "INSERT INTO interaction_payloads (content_hash, content_kind, content, byte_size) "
        "VALUES (%s, %s, %s::jsonb, %s) ON CONFLICT (content_hash) DO NOTHING",
        (pl.content_hash, pl.content_kind, json.dumps(pl.content, default=str), pl.byte_size),
    )
    return pl.content_hash


def _legs_of(row: _Row) -> list[tuple[str, Any, str | None, bool | None]]:
    """Project one row into OBSERVED legs: (leg_type, occurred_at, payload_hash,
    error). The request leg always exists; the response leg only when the
    response span does — its absence IS the in-flight signal (never fabricated,
    matching the graph adapter's rule).

    Request-leg ``error`` is None: the wire carries no request-side outcome —
    ``lineage.outcome`` is a completion fact and belongs to the response leg (a
    consumer acting on the request leg must not read a verdict that arrived
    later as if it were request-time).

    Leg ``seq`` is not projected here: it is DB-owned (``nextval``, the
    migration-0009 DEFAULT) and assigned once at first INSERT. Request-seq <
    response-seq holds structurally: a response leg is only derivable once its
    request span exists, and the request leg is inserted first — either in the
    same transaction (request emitted first) or in an earlier drain (response
    still in flight)."""
    legs: list[tuple[str, Any, str | None, bool | None]] = [(
        "request",
        row.started_at,
        row.request_payload.content_hash if row.request_payload else None,
        None,
    )]
    if row.response_span_id is not None:
        legs.append((
            "response",
            row.ended_at,
            row.response_payload.content_hash if row.response_payload else None,
            row.error,
        ))
    return legs


def _write(tx: db.Transaction, plan: _Plan) -> None:
    """Apply a :class:`_Plan` to the DB and delete this trace's derived rows not
    justified by it. There are no FK constraints on these tables (ADR-0014 /
    migration 0004), so the order below is for clarity, not referential safety —
    with one real ordering constraint: stale legs are deleted via a subselect on
    ``interactions`` (legs carry no trace_id), so that delete must run while the
    stale parent rows still exist."""
    trace_id, want, anchor_ids = plan.trace_id, plan.want, plan.anchor_ids
    # 1. entities + payloads.  Canonical pod entities are global and upserted.
    #    A host:port agent/tool created before its inbound echo is provisional;
    #    it is retired at the end of reconciliation once nothing references it.
    entity_id_of: dict[str, str] = {}
    for row in want.values():
        for ent in (row.caller, row.callee):
            entity_id_of[ent.natural_key] = _upsert_entity(tx, ent, row.seq)
        _upsert_payload(tx, row.request_payload)
        _upsert_payload(tx, row.response_payload)

    # 2. interactions — the shared identity row (ADR-0025: no timings, no
    #    payload hashes, no seq — those live on the legs). Two passes so
    #    parent_interaction_id (another anchor in `want`) is always set to a
    #    live row, before any stale interaction is deleted below.
    for row in want.values():
        tx.execute(
            "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
            "caller_entity_id, callee_entity_id, summary) "
            "VALUES (%s, %s, NULL, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "caller_entity_id = EXCLUDED.caller_entity_id, "
            "callee_entity_id = EXCLUDED.callee_entity_id, "
            "summary = EXCLUDED.summary",
            (
                row.interaction_id, trace_id,
                entity_id_of[row.caller.natural_key], entity_id_of[row.callee.natural_key],
                # Namespace-qualified: the two pods this change tells apart
                # must read apart here too, not only in natural_key.
                f"{row.caller.ident_key} → {row.callee.ident_key}",
            ),
        )
    for row in want.values():
        parent_ix = (
            _interaction_id(trace_id, row.parent_anchor_span_id)
            if row.parent_anchor_span_id is not None
            else None
        )
        tx.execute(
            "UPDATE interactions SET parent_interaction_id = %s "
            "WHERE id = %s AND parent_interaction_id IS DISTINCT FROM %s",
            (parent_ix, row.interaction_id, parent_ix),
        )

    # 3. interaction_legs — observed legs (see _legs_of). ``seq`` is OMITTED so
    #    the column DEFAULT (nextval, migration 0009) assigns it once at first
    #    INSERT, and it is absent from DO UPDATE so a re-derive preserves the
    #    once-assigned value — the post-#123 DB-owned model shared with the
    #    streaming branch of state.flush (only cosmetic nextval gaps on replay).
    for row in want.values():
        for leg_type, occurred_at, payload_hash, error in _legs_of(row):
            tx.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, "
                "occurred_at, payload_hash, error) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
                "occurred_at = EXCLUDED.occurred_at, "
                "payload_hash = EXCLUDED.payload_hash, "
                "error = EXCLUDED.error",
                (row.interaction_id, leg_type, occurred_at, payload_hash,
                 error),
            )

    # 4. Delete this trace's interactions no longer justified by `want` — the
    #    reconcile's shrink case (e.g. an inbound entry demoted to echo once its
    #    outbound ancestor arrived). Legs first, via the still-live parent rows.
    keep = list(want.keys())
    if keep:
        ph = ", ".join(["%s"] * len(keep))
        tx.execute(
            f"DELETE FROM interaction_legs WHERE interaction_id IN "
            f"(SELECT id FROM interactions WHERE trace_id = %s AND id NOT IN ({ph}))",
            [trace_id, *keep],
        )
        tx.execute(
            f"DELETE FROM interactions WHERE trace_id = %s AND id NOT IN ({ph})",
            [trace_id, *keep],
        )
    else:
        tx.execute(
            "DELETE FROM interaction_legs WHERE interaction_id IN "
            "(SELECT id FROM interactions WHERE trace_id = %s)",
            (trace_id,),
        )
        tx.execute("DELETE FROM interactions WHERE trace_id = %s", (trace_id,))

    # 5. interaction_spans — rewrite wholesale for the trace: each anchor is an
    #    'anchor' row; every other span is a 'connector' of its nearest-anchor
    #    interaction (response spans, callee echoes, and bridge spans all land
    #    on the enclosing interaction). Deletes stale rows implicitly.
    #    leg_type marks a span that is DIRECT evidence of a specific leg — the
    #    request span and the paired response span; echo/bridge/other connector
    #    spans evidence the interaction's territory, not either leg, and stay
    #    NULL (the column is nullable for exactly this, ADR-0025).
    response_leg_span_of: dict[str, str] = {
        row.response_span_id: row.anchor_span_id
        for row in want.values()
        if row.response_span_id is not None
    }
    tx.execute("DELETE FROM interaction_spans WHERE trace_id = %s", (trace_id,))
    for aid in anchor_ids:
        tx.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES (%s, %s, %s, 'anchor', 'request')",
            (_interaction_id(trace_id, aid), trace_id, aid),
        )
    for owner, span_id in plan.connectors:
        leg_type = (
            "response" if response_leg_span_of.get(span_id) == owner else None
        )
        # Deliberately NO ON CONFLICT, matching the anchor insert above: the
        # trace-scoped wipe plus one-row-per-span iteration make a conflict
        # unreachable single-writer, so a conflict can only mean a concurrent
        # writer — and the old DO UPDATE would have silently downgraded its
        # anchor row to connector. Same invariant, same loud failure.
        tx.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES (%s, %s, %s, 'connector', %s)",
            (_interaction_id(trace_id, owner), trace_id, span_id, leg_type),
        )

    # 6. entity_spans — trace-scoped rewrite: each interaction's endpoints are
    #    discovered_via its anchor span.
    tx.execute("DELETE FROM entity_spans WHERE trace_id = %s", (trace_id,))
    for row in want.values():
        for ent in (row.caller, row.callee):
            tx.execute(
                "INSERT INTO entity_spans (entity_id, trace_id, span_id, role) "
                "VALUES (%s, %s, %s, 'discovered_via') "
                "ON CONFLICT (trace_id, span_id, entity_id, role) DO NOTHING",
                (entity_id_of[ent.natural_key], trace_id, row.anchor_span_id),
            )

    # 7. Retire only clearly provisional agent/tool peer identities after every
    #    trace-scoped reference has been reconciled.  The two NOT EXISTS guards
    #    make this safe across traces and preserve evidence-only entities.  Do
    #    not apply the host:port heuristic to service/llm rows: those are stable
    #    endpoint identities by contract.
    tx.execute(
        "DELETE FROM entities e "
        "WHERE e.namespace IS NULL "
        "AND e.kind IN ('agent', 'tool') "
        "AND e.natural_key ~ '^(agent|tool):[^/[:space:]]+:[0-9]+$' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM interactions i "
        "  WHERE i.caller_entity_id = e.id OR i.callee_entity_id = e.id"
        ") "
        "AND NOT EXISTS (SELECT 1 FROM entity_spans es WHERE es.entity_id = e.id)"
    )
