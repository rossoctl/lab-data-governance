"""DB-backed state layer for the P-interactions processor.

The verified prototype (``procedure.Processor``) accumulates all state in
memory and, because spans are fed in ``seq`` order and never pre-loaded, runs
each ``process(S)`` against exactly the arrived set ``{spans : seq <= S.seq}``.
This module reproduces that view from the database, per span, so the verbatim
classification can run unchanged:

  1. ``rehydrate(tx, proc, span)`` — load span S's lineage (ancestors ∪ subtree,
     ``seq <= S.seq``) into the processor's span-view dicts, and the existing
     derived rows for that lineage region into its entity / interaction /
     interaction_spans dicts. S itself is deliberately NOT pre-inserted into
     ``spans_by_id`` — ``process(S)`` adds it, so it goes through the normal
     arrival path (``is_finalization=False`` → ``_dispatch``).
  2. the caller runs ``proc.process(span)`` (verbatim).
  3. ``flush(tx, proc, span, region)`` — write the re-derived region back:
     upsert entities / payloads / entity_spans and, per ADR-0025, project each
     in-memory interaction into its parent identity row + request/response
     ``interaction_legs`` rows (the boundary projection — ``procedure.py`` still
     holds one interaction per anchor), and region-scoped delete+reinsert of the
     info/connector interaction_spans (anchors are emit-once and never deleted),
     then advance the cursor.

The lineage horizon (``seq <= S.seq``) is the load-bearing line: it makes the
DB-backed processor see exactly the in-memory prototype's arrived set, so the
verified ``--scramble`` order-independence transfers. Per the decisions for
issue #70, ``seq`` is used for BOTH the cursor and the horizon (one column);
the two-column ``arrival_seq`` split is deferred until finalization is
exercised (slice #73 adds the tripwire).
"""

from __future__ import annotations

from typing import Any

from data_governance import db
from data_governance.retrieval import Span

# _COLUMNS / _row_to_span are private helpers of the spans submodule; imported
# from there directly (not via the package root) to name the reach-in.
from data_governance.retrieval.spans import _COLUMNS, _row_to_span

from . import procedure

PROCESSOR_NAME = "interactions"

# --- leg projection (ADR-0025) ----------------------------------------------
# The one place the request/response <-> (timestamp, payload) mapping lives, so
# the flush projection and the rehydrate fold-back cannot drift apart. The
# current (Case-X) source has one in-memory ProtoInteraction; the request leg
# carries its start-side (started_at, request payload), the response leg its
# end-side (ended_at, response payload). error / seq / original_seq are shared
# across both derived legs (see the Leg-provenance term in CONTEXT.md).


def _legs_of(ix: procedure.ProtoInteraction) -> list[tuple[str, Any, str | None]]:
    """Forward projection: one interaction -> [(leg_type, occurred_at,
    payload_hash), ...], request leg first. Both legs are always emitted so the
    parent is never leg-less (ADR-0025)."""
    return [
        ("request", ix.started_at, ix.request_payload_hash),
        ("response", ix.ended_at, ix.response_payload_hash),
    ]


_SELECT_COLS = ", ".join(_COLUMNS)

# Ancestors of S (self ∪ chain to the root), restricted to the arrived set.
# Walks parent_id up via the (trace_id, span_id) PK index.
_ANCESTORS_SQL = f"""
WITH RECURSIVE anc AS (
    SELECT {_SELECT_COLS} FROM spans
     WHERE trace_id = %(tid)s AND span_id = %(sid)s AND seq <= %(cur)s
    UNION ALL
    SELECT {", ".join("s." + c for c in _COLUMNS)} FROM spans s
      JOIN anc a ON s.trace_id = a.trace_id AND s.span_id = a.parent_id
     WHERE s.seq <= %(cur)s
)
SELECT {_SELECT_COLS} FROM anc
"""

# Subtree rooted at a span (self ∪ all descendants), restricted to the arrived
# set. Walks parent_id down via the (trace_id, parent_id) index.
_SUBTREE_SQL = f"""
WITH RECURSIVE sub AS (
    SELECT {_SELECT_COLS} FROM spans
     WHERE trace_id = %(tid)s AND span_id = %(sid)s AND seq <= %(cur)s
    UNION ALL
    SELECT {", ".join("s." + c for c in _COLUMNS)} FROM spans s
      JOIN sub d ON s.trace_id = d.trace_id AND s.parent_id = d.span_id
     WHERE s.seq <= %(cur)s
)
SELECT {_SELECT_COLS} FROM sub
"""


def _fetch_spans(tx: db.Transaction, sql: str, trace_id: str, span_id: str, cur: int) -> list[Span]:
    rows = tx.fetch_all(sql, {"tid": trace_id, "sid": span_id, "cur": cur})
    return [_row_to_span(r, in_time_window=True) for r in rows]


class _LazyChildren(dict):
    """Children index that fetches a span's subtree from the DB on first miss.

    The prototype's ``self.children`` is a fully-populated ``{parent_id:
    [child]}`` map. We pre-seed the lineage region, but ``_repair_after_arrival``
    can walk the subtree of an ancestor anchor that just emitted (a region not
    necessarily pre-loaded — the R3 case). Backing ``.get()`` with a subtree
    CTE on demand keeps the walks faithful without loading the whole trace, and
    bounds memory to what is actually walked.
    """

    def __init__(self, tx: db.Transaction, trace_id: str, horizon: int) -> None:
        super().__init__()
        self._tx = tx
        self._trace_id = trace_id
        self._horizon = horizon
        # span_ids whose subtree has been materialised into this map already.
        self._loaded: set[str] = set()

    def _ensure(self, span_id: str | None) -> None:
        if span_id is None or span_id in self._loaded:
            return
        self._loaded.add(span_id)
        subtree = _fetch_spans(self._tx, _SUBTREE_SQL, self._trace_id, span_id, self._horizon)
        for s in subtree:
            if s.span_id == span_id:
                continue  # the root itself is not its own child
            bucket = super().setdefault(s.parent_id, [])
            if all(existing.span_id != s.span_id for existing in bucket):
                bucket.append(s)
            self._loaded.add(s.span_id)  # its children are in this same subtree

    def get(self, key: str | None, default: Any = None) -> Any:  # type: ignore[override]
        self._ensure(key)
        return super().get(key, default if default is not None else [])


def _index_span(proc: procedure.Processor, span: Span) -> None:
    proc.spans_by_id[(span.trace_id, span.span_id)] = span
    proc._span_by_id_index[span.span_id] = span
    if span.kind == "SERVER" and (span.name or "").upper().startswith("POST /MCP"):
        if span.service_name:
            proc._mcp_services.add(span.service_name)
    canon = _canonical(span)
    if canon:
        proc.known_canonicals.add(canon)


def _canonical(span: Span) -> str | None:
    from .caller_inference import canonical_service_name

    return canonical_service_name(span)


def rehydrate(tx: db.Transaction, proc: procedure.Processor, span: Span) -> list[str]:
    """Seed *proc* with span S's lineage + the derived rows for that region.

    Returns the lineage span_id list (the region the subsequent flush is
    authoritative for). S itself is included in the span-view index EXCEPT
    in ``spans_by_id``, which ``process(S)`` populates so S takes the arrival
    path. The horizon is ``S.seq``.
    """
    horizon = span.seq
    trace_id = span.trace_id

    # --- span view: ancestors ∪ subtree, seq <= S.seq -----------------------
    ancestors = _fetch_spans(tx, _ANCESTORS_SQL, trace_id, span.span_id, horizon)
    subtree = _fetch_spans(tx, _SUBTREE_SQL, trace_id, span.span_id, horizon)

    lineage: dict[str, Span] = {}
    for s in (*ancestors, *subtree):
        lineage[s.span_id] = s

    # A span's children bucket pre-seeded below is COMPLETE only when every one
    # of its children (at the horizon) is in the lineage. That holds for spans in
    # S's SUBTREE — the subtree CTE returns each span together with all its
    # descendants ≤ horizon — but NOT for S's ANCESTORS, whose off-path branches
    # are not loaded. So only subtree spans may be marked ``_loaded`` (which tells
    # ``_LazyChildren`` "your subtree is materialised, skip the fetch"). Marking an
    # ancestor ``_loaded`` would suppress the lazy fetch and leave a later repair
    # that walks that ancestor's subtree (e.g. an orphan-server edge emitting on a
    # late OI-AGENT arrival, whose anchor is the enclosing root) with an empty
    # bucket — silently dropping that root's other-branch territory spans. That
    # was the #72 arrival-order divergence: in-order the root is processed last
    # (full horizon, everything pre-seeded), but under late-parent arrival the
    # root's subtree must be fetched on demand.
    subtree_ids = {s.span_id for s in subtree}

    # children fetches subtrees lazily (handles repair-root subtrees, R3).
    proc.children = _LazyChildren(tx, trace_id, horizon)

    for s in lineage.values():
        # Index every lineage span EXCEPT S into spans_by_id; process(S) will
        # add S (and the children bucket) itself, taking the arrival path.
        if s.span_id == span.span_id:
            # still index S's identity lookups + canonicals/mcp signal, but not
            # spans_by_id (process() owns that, to keep is_finalization False).
            proc._span_by_id_index[s.span_id] = s
            if s.kind == "SERVER" and (s.name or "").upper().startswith("POST /MCP"):
                if s.service_name:
                    proc._mcp_services.add(s.service_name)
            c = _canonical(s)
            if c:
                proc.known_canonicals.add(c)
            continue
        _index_span(proc, s)
        proc.children.setdefault(s.parent_id, [])
        bucket = dict.get(proc.children, s.parent_id) or []
        if all(existing.span_id != s.span_id for existing in bucket):
            dict.setdefault(proc.children, s.parent_id, []).append(s)
        # Mark as already-materialised ONLY when this span's full subtree was
        # pre-seeded (it lies in S's subtree). Ancestors are left un-loaded so a
        # later subtree walk of an ancestor triggers a real, complete CTE fetch.
        if s.span_id in subtree_ids:
            proc.children._loaded.add(s.span_id)

    lineage_ids = list(lineage.keys())

    # --- derived state ------------------------------------------------------
    _rehydrate_derived(tx, proc, trace_id, horizon, lineage_ids)
    return lineage_ids


def _rehydrate_derived(
    tx: db.Transaction,
    proc: procedure.Processor,
    trace_id: str,
    horizon: int,
    lineage_ids: list[str],
) -> None:
    """Load the derived rows for S's LINEAGE region into the processor's dicts.

    Scope: interactions whose primary anchor span is in S's lineage (ancestors ∪
    subtree), with anchor ``seq <= horizon``. This is the tight, faithful scope —
    every anchor the per-span procedure consults via ``_innermost_owner_for`` /
    ``_compute_parent_interaction`` lies on the ancestor chain of a span in the
    repair region, and the repair region is bounded by S's lineage. (Verified on
    the 281-span trace: across every span, zero consulted anchors fall outside
    its lineage — so the prototype's lineage-locality holds, and no trace-wide
    load is needed.)

    Each interaction's TRUE primary anchor span is read from its own ``anchor``
    interaction_spans row (emit-once → exactly one), never inferred from lineage
    membership — so an interaction whose anchor is in the lineage keys correctly
    regardless of which of its territory spans triggered the load.

    Horizon: an interaction is "arrived" iff its primary anchor span has arrived
    (``seq <= horizon``), so a cursor-reset re-run sees exactly the arrived set
    the forward drain saw — the order-independence invariant.
    """
    if not lineage_ids:
        return
    lph = ", ".join(["%s"] * len(lineage_ids))

    # Interactions anchored at a lineage span (seq <= horizon). anchor_of maps
    # interaction_id -> its primary anchor span_id.
    anchor_rows = tx.fetch_all(
        f"SELECT isp.interaction_id, isp.span_id "
        f"FROM interaction_spans isp JOIN spans s "
        f"  ON s.trace_id = isp.trace_id AND s.span_id = isp.span_id "
        f"WHERE isp.trace_id = %s AND isp.role = 'anchor' "
        f"  AND s.seq <= %s AND isp.span_id IN ({lph})",
        [trace_id, horizon, *lineage_ids],
    )
    # An interaction can have MORE than one `anchor`-role span: a cross-service
    # edge anchors BOTH its CLIENT egress and the callee's SERVER side (see
    # `_emit_cross_service`, anchor_span_ids=(parent, span)). Only ONE of them is
    # the PRIMARY anchor — the span whose id seeds the interaction id
    # (`_interaction_id(trace_id, primary_span_id)`), which is what
    # `interactions_by_anchor` and `_innermost_owner_for` key on. Picking an
    # arbitrary anchor row here (e.g. last-wins on a dict comprehension) keys the
    # interaction under a NON-primary anchor, so `_innermost_owner_for` — which
    # looks the interaction up by its primary anchor span on the ancestor chain —
    # misses it and wrongly detaches the spans in its territory. The miss is
    # arrival-order-sensitive (which anchor row the query returns first), which is
    # the #72 divergence. Recover the TRUE primary by reproducing the interaction
    # id from each candidate anchor span.
    anchor_candidates: dict[str, list[str]] = {}
    for ix_id, sid in anchor_rows:
        anchor_candidates.setdefault(ix_id, []).append(sid)
    anchor_of: dict[str, str] = {}
    for ix_id, sids in anchor_candidates.items():
        primary = next(
            (s for s in sids if procedure._interaction_id(trace_id, s) == ix_id),
            sids[0],  # fall back to a stable choice if none reproduces the id
        )
        anchor_of[ix_id] = primary
    visible_ix: set[str] = set(anchor_of)

    # All interaction_spans rows of those interactions (their territory may
    # extend beyond the lineage span_ids; load the whole interaction's spans so
    # _owners_by_span / _attached_span_ids are complete for re-derivation).
    if visible_ix:
        iph = ", ".join(["%s"] * len(visible_ix))
        ispans = tx.fetch_all(
            f"SELECT interaction_id, trace_id, span_id, role FROM interaction_spans "
            f"WHERE interaction_id IN ({iph})",
            list(visible_ix),
        )
        for ix_id, tid, sid, role in ispans:
            proc.interaction_spans.append(procedure.ProtoInteractionSpan(ix_id, tid, sid, role))
            proc._owners_by_span[sid] = ix_id
            proc._role_by_span[sid] = role
            proc._attached_span_ids.setdefault(ix_id, set()).add(sid)

    if visible_ix:
        iph = ", ".join(["%s"] * len(visible_ix))
        # Identity from the parent interactions row (ADR-0025).
        irows = tx.fetch_all(
            f"SELECT id, trace_id, parent_interaction_id, caller_entity_id, "
            f"callee_entity_id, summary "
            f"FROM interactions WHERE id IN ({iph})",
            list(visible_ix),
        )
        # Leg-dependent fields from interaction_legs — the read half of the flush
        # boundary projection: fold the request leg back into started_at /
        # request_payload_hash and the response leg into ended_at /
        # response_payload_hash, so the in-memory ProtoInteraction stays the
        # single-row shape procedure.py expects (unchanged). error/seq/original_seq
        # are shared across the derived legs; take them off whichever leg carries
        # them (prefer the request leg's seq — the one the flush stamped).
        legrows = tx.fetch_all(
            f"SELECT interaction_id, leg_type::text, occurred_at, payload_hash, "
            f"error, seq, original_seq FROM interaction_legs "
            f"WHERE interaction_id IN ({iph})",
            list(visible_ix),
        )
        legs_by_ix: dict[str, dict[str, tuple]] = {}
        for ix_id, leg_type, occurred_at, payload_hash, err, seq, oseq in legrows:
            legs_by_ix.setdefault(ix_id, {})[leg_type] = (
                occurred_at, payload_hash, err, seq, oseq,
            )
        for ix_id, tid, parent, caller, callee, summary in irows:
            primary_anchor = anchor_of.get(ix_id)
            if primary_anchor is None:
                continue  # no anchor row — data-integrity surprise, skip
            legs = legs_by_ix.get(ix_id, {})
            req = legs.get("request")
            resp = legs.get("response")
            # seq / original_seq / error are shared across the current source's
            # derived legs; the request leg is authoritative (both were written
            # with the same value), fall back to the response leg then defaults.
            authoritative = req or resp
            seq = authoritative[3] if authoritative else 0
            oseq = authoritative[4] if authoritative else 0
            err = authoritative[2] if authoritative else None
            proc.interactions_by_anchor[primary_anchor] = procedure.ProtoInteraction(
                id=ix_id,
                trace_id=tid,
                parent_interaction_id=parent,
                caller_entity_id=caller,
                callee_entity_id=callee,
                started_at=req[0] if req else None,
                ended_at=resp[0] if resp else None,
                error=err,
                request_payload_hash=req[1] if req else None,
                response_payload_hash=resp[1] if resp else None,
                summary=summary,
                seq=seq,
                original_seq=oseq,
                anchor_rule="",
                primary_anchor_span_id=primary_anchor,
            )

    # entity_spans for the lineage region (seq <= horizon) → entity_spans list.
    espans = tx.fetch_all(
        f"SELECT es.entity_id, es.trace_id, es.span_id, es.role "
        f"FROM entity_spans es JOIN spans s "
        f"  ON s.trace_id = es.trace_id AND s.span_id = es.span_id "
        f"WHERE es.trace_id = %s AND s.seq <= %s AND es.span_id IN ({lph})",
        [trace_id, horizon, *lineage_ids],
    )
    entity_ids: set[str] = set()
    for eid, tid, sid, role in espans:
        proc.entity_spans.append(procedure.ProtoEntitySpan(eid, tid, sid, role))
        entity_ids.add(eid)
    # Entities referenced by the loaded interactions too (their identity rows are
    # needed so _resolve_caller_around_oi_span etc. resolve).
    for ix in proc.interactions_by_anchor.values():
        entity_ids.add(ix.caller_entity_id)
        entity_ids.add(ix.callee_entity_id)

    if entity_ids:
        eph = ", ".join(["%s"] * len(entity_ids))
        erows = tx.fetch_all(
            f"SELECT id, kind, natural_key, display_name, project_name, "
            f"detected_from, seq, original_seq FROM entities WHERE id IN ({eph})",
            list(entity_ids),
        )
        for eid, kind, nk, disp, proj, detected, seq, oseq in erows:
            proc.entities[nk] = procedure.ProtoEntity(
                id=eid,
                kind=kind,
                natural_key=nk,
                display_name=disp,
                project_name=proj,
                detected_from=detected,
                first_seen_seq=oseq,
                seq=seq,
                original_seq=oseq,
            )

        # _entity_first_span must be seeded for EVERY loaded entity that already
        # has a discovered_via row (created by ANY arrived span, not just one in
        # this lineage) — otherwise _upsert_entity would append a duplicate
        # discovered_via on a different span and diverge. Query by entity id, not
        # by lineage span, so an entity discovered outside this region is still
        # recognised as already-discovered.
        first_rows = tx.fetch_all(
            f"SELECT DISTINCT es.entity_id "
            f"FROM entity_spans es JOIN spans s "
            f"  ON s.trace_id = es.trace_id AND s.span_id = es.span_id "
            f"WHERE es.role = 'discovered_via' AND s.seq <= %s "
            f"  AND es.entity_id IN ({eph})",
            [horizon, *entity_ids],
        )
        for (eid,) in first_rows:
            proc._entity_first_span.add(eid)

    # _tool_anchor_entity_by_span: an OI-TOOL anchor span maps to its tool
    # entity. Reconstruct from the loaded interactions whose callee is a tool
    # entity, keyed by their anchor span. (Used by cross-service caller
    # resolution.)
    for asid, ix in proc.interactions_by_anchor.items():
        if not asid:
            continue
        callee = next(
            (e for e in proc.entities.values() if e.id == ix.callee_entity_id), None
        )
        if callee is not None and callee.kind == "tool":
            proc._tool_anchor_entity_by_span[asid] = callee.id


def flush(tx: db.Transaction, proc: procedure.Processor, span: Span) -> None:
    """Write the re-derived region back to the DB, within *tx*.

    The interaction_spans delete-scope is ``proc._repaired_span_ids`` (the spans
    whose ownership this dispatch actually re-derived), NOT the loaded lineage.
    No FKs between derived tables (#69), so no parent-null two-pass is needed.

    The durable cursor advance is NOT written here (issue #75): the shared drain
    loop advances ``processor_state`` in this same *tx* after ``flush`` returns,
    so the advance still commits atomically with these derived writes (ADR-0007).
    """
    # 1. entities — id is deterministic (uuid5 of natural_key), so re-derives
    #    collapse on natural_key.
    for e in proc.entities.values():
        tx.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "project_name, detected_from, seq, original_seq) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (natural_key) DO UPDATE SET "
            "display_name = EXCLUDED.display_name, "
            "project_name = EXCLUDED.project_name, "
            "detected_from = EXCLUDED.detected_from, "
            "seq = EXCLUDED.seq",
            (e.id, e.kind, e.natural_key, e.display_name, e.project_name,
             e.detected_from, e.seq, e.original_seq),
        )

    # 2. payloads — content-addressed.
    import json as _json

    for p in proc.payloads.values():
        tx.execute(
            "INSERT INTO interaction_payloads (content_hash, content_kind, content, byte_size) "
            "VALUES (%s, %s, %s::jsonb, %s) ON CONFLICT (content_hash) DO NOTHING",
            (p.content_hash, p.content_kind, _json.dumps(p.content, default=str), p.byte_size),
        )

    # 3. interactions + interaction_legs (ADR-0025 boundary projection).
    #    The verified in-memory ProtoInteraction is still ONE object per anchor;
    #    the split into a parent identity row + a request leg + a response leg
    #    happens HERE, at the write boundary, so procedure.py is unchanged.
    #    id / seq deterministic; parent_interaction_id written directly.
    #
    #    Parent = identity shared across both legs (id, trace_id,
    #    parent_interaction_id, caller/callee, summary). No seq on the parent.
    for ix in proc.interactions_by_anchor.values():
        tx.execute(
            "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
            "caller_entity_id, callee_entity_id, summary) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "parent_interaction_id = EXCLUDED.parent_interaction_id, "
            "caller_entity_id = EXCLUDED.caller_entity_id, "
            "callee_entity_id = EXCLUDED.callee_entity_id, "
            "summary = EXCLUDED.summary",
            (ix.id, ix.trace_id, ix.parent_interaction_id, ix.caller_entity_id,
             ix.callee_entity_id, ix.summary),
        )
        # Legs via the shared `_legs_of` projection (request first). For the
        # current (Case-X) source both legs derive from the one span, so they
        # share the interaction's seq and its error (a "derived" leg — the
        # request/response timings bracket the one synchronous call; see the
        # Leg-provenance term in CONTEXT.md). occurred_at may be NULL if the span
        # had no start/end yet; the authoritative recompute (step 4b) folds the
        # leg's territory in.
        for leg_type, occurred_at, payload_hash in _legs_of(ix):
            tx.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, "
                "occurred_at, payload_hash, error, seq, original_seq) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (interaction_id, leg_type) DO UPDATE SET "
                "occurred_at = EXCLUDED.occurred_at, "
                "payload_hash = EXCLUDED.payload_hash, "
                "error = EXCLUDED.error, seq = EXCLUDED.seq",
                (ix.id, leg_type, occurred_at, payload_hash, ix.error,
                 ix.seq, ix.original_seq),
            )

    # 4. interaction_spans — scoped to the span_ids _repair_after_arrival
    #    actually re-derived this dispatch (proc._repaired_span_ids), NOT the
    #    whole loaded lineage. This is risk R1: when re-processing an early span
    #    against a fully-populated DB, the loaded lineage spans most of the trace,
    #    but this span's repair only re-derives its own (small) region — deleting
    #    the broader region would clobber info/connector rows that a higher-seq
    #    span legitimately owns. Delete the repaired set's info/connector rows
    #    (anchors are emit-once, never deleted), then upsert the rows the
    #    processor holds for that set. A span the repair detached (no owner now)
    #    is deleted and not reinserted.
    repaired = proc._repaired_span_ids
    if repaired:
        ph = ", ".join(["%s"] * len(repaired))
        tx.execute(
            f"DELETE FROM interaction_spans WHERE trace_id = %s "
            f"AND role IN ('info', 'connector') AND span_id IN ({ph})",
            [span.trace_id, *repaired],
        )
    for r in proc.interaction_spans:
        # Always upsert anchor rows (emit-once). Upsert info/connector rows only
        # for the repaired set — rows for spans outside it were loaded unchanged
        # from the DB and re-writing them is a harmless no-op we skip for clarity.
        if r.role != "anchor" and r.span_id not in repaired:
            continue
        # leg_type (ADR-0025): the current (Case-X) source has one span per
        # interaction carrying both payloads, so its evidence attributes to the
        # 'request' leg — the primary. The future (Case-Y) source, whose request
        # and response spans are distinct, will stamp each span with its own leg.
        tx.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES (%s, %s, %s, %s, 'request') "
            "ON CONFLICT (trace_id, span_id) DO UPDATE SET "
            "interaction_id = EXCLUDED.interaction_id, role = EXCLUDED.role, "
            "leg_type = EXCLUDED.leg_type",
            (r.interaction_id, r.trace_id, r.span_id, r.role),
        )

    # 4b. Authoritative aggregate recompute, now folded onto the LEGS (ADR-0025).
    #
    # The in-memory fold in _update_aggregates keeps ix's window monotonic, but
    # it can only fold spans that are in the arriving span's lineage index. A
    # connector/info span attached to an interaction via the lazy children walk
    # (state.rehydrate never indexes it) is structurally invisible to that fold,
    # so under scrambled arrival the window can stay narrower than the in-order
    # drain (e.g. the root's max ended_at lives on an off-lineage child). Recompute
    # the window here from the AUTHORITATIVE, arrival-independent join over the
    # persisted interaction_spans — every attached span contributes exactly once,
    # regardless of whether it was ever in the in-memory index.
    #
    # The leg projection: the request leg's occurred_at is the interaction's
    # min(started_at) over its territory; the response leg's occurred_at is the
    # max(ended_at). error folds onto BOTH legs (the current derived-leg source
    # shares one error across the synchronous call). We compute the aggregate
    # once and update both leg rows.
    #
    # The `s.seq <= %s` horizon (= this span's seq) is mandatory: it mirrors the
    # rehydrate horizon (risk R1 above) so re-processing an early span against a
    # fully-populated DB sees exactly its arrived set, not the whole trace.
    # LEAST/GREATEST skip NULLs and are monotonic, so this can only agree with or
    # widen the persisted window — never narrow it.
    for ix in proc.interactions_by_anchor.values():
        tx.execute(
            "UPDATE interaction_legs AS l SET "
            "occurred_at = CASE l.leg_type "
            "    WHEN 'request' THEN LEAST(l.occurred_at, agg.min_started) "
            "    ELSE GREATEST(l.occurred_at, agg.max_ended) END, "
            "error = CASE WHEN agg.any_error IS TRUE OR l.error IS TRUE THEN TRUE "
            "            WHEN agg.any_error IS FALSE OR l.error IS FALSE THEN FALSE "
            "            ELSE l.error END "
            "FROM (SELECT min(s.started_at) AS min_started, "
            "             max(s.ended_at) AS max_ended, "
            "             bool_or(s.error) AS any_error "
            "      FROM interaction_spans isp "
            "      JOIN spans s ON s.trace_id = isp.trace_id "
            "                  AND s.span_id = isp.span_id "
            "      WHERE isp.interaction_id = %s AND s.seq <= %s) AS agg "
            "WHERE l.interaction_id = %s",
            (ix.id, span.seq, ix.id),
        )

    # 5. entity_spans — append-only.
    #
    # NOTE (#72): the discovered_via / identified_via split is provenance only —
    # it does not affect entities, interactions, payloads, or interaction_spans
    # (the graph). Because each span re-derives in its own lineage region, "first
    # to touch an entity" is processing-order-sensitive, so this split is NOT
    # order-independent and can carry duplicate discovered_via rows. Making it
    # deterministic needs an arrival-invariant per-span order (an ingest seq the
    # scramble preserves), which is its own concern — the #72 byte-gate therefore
    # covers the four graph tables and asserts entity_spans on total count only.
    for es in proc.entity_spans:
        tx.execute(
            "INSERT INTO entity_spans (entity_id, trace_id, span_id, role) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (trace_id, span_id, entity_id, role) DO NOTHING",
            (es.entity_id, es.trace_id, es.span_id, es.role),
        )

    # NOTE: the durable cursor advance is intentionally NOT written here — the
    # shared drain loop (:func:`data_governance.processors._driver.drain`)
    # advances ``processor_state`` in this SAME transaction after ``flush``
    # returns, so a crash mid-span still commits nothing (ADR-0007 recovery).
