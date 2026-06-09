"""Tests for ``get_edges`` (issue #57 / ADR-0007).

Mirrors the ``get_spans`` test discipline: real Postgres, frozen-dataclass
mapping, cursor/limit/order, and the keyset invariant — with the slice-specific
twist that ``get_edges`` cursors on ``edge_seq`` (sort axis == cursor axis) and
reads per-edge timing by joining ``spans`` (no denormalized columns).
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from data_governance import retrieval


@pytest.fixture()
def insert_entity(raw_conn):
    def _ins(
        entity_id: str,
        *,
        service_name: str | None = None,
        semantic_kind: str = "UNKNOWN",
        sub_kind: str | None = None,
        display_name: str | None = None,
        attributes: dict | None = None,
    ) -> None:
        raw_conn.execute(
            "INSERT INTO entities (entity_id, service_name, semantic_kind, "
            "sub_kind, display_name, attributes, first_seen_at, last_seen_at) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, now(), now())",
            (
                entity_id,
                service_name,
                semantic_kind,
                sub_kind,
                display_name,
                json.dumps(attributes) if attributes is not None else None,
            ),
        )
        raw_conn.commit()

    return _ins


@pytest.fixture()
def insert_edge(raw_conn):
    def _ins(
        *,
        trace_id: str,
        span_id: str,
        parent_id: str,
        to_entity: str,
        from_entity: str | None = None,
        edge_kind: str | None = None,
    ) -> None:
        raw_conn.execute(
            "INSERT INTO edges (trace_id, span_id, parent_id, to_entity, "
            "from_entity, edge_kind) VALUES (%s, %s, %s, %s, %s, %s)",
            (trace_id, span_id, parent_id, to_entity, from_entity, edge_kind),
        )
        raw_conn.commit()

    return _ins


_T0 = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


class TestGetEdgesBasics:
    def test_maps_full_dataclass_with_joined_timing(
        self, configured_db, insert_entity, insert_edge, insert_span
    ) -> None:
        insert_entity("from_e", semantic_kind="CHAIN")
        insert_entity("to_e", semantic_kind="LLM", sub_kind="gpt-4")
        started = _T0
        ended = _T0 + dt.timedelta(seconds=3)
        # The child span carries the timing; the edge does not.
        insert_span(
            trace_id="t", span_id="c", name="llm", parent_id="p",
            started_at=started, ended_at=ended, kind="INTERNAL",
        )
        insert_edge(
            trace_id="t", span_id="c", parent_id="p",
            from_entity="from_e", to_entity="to_e", edge_kind="CHAIN_LLM",
        )

        result = retrieval.get_edges()
        assert len(result.edges) == 1
        e = result.edges[0]
        assert isinstance(e, retrieval.Edge)
        assert e.trace_id == "t"
        assert e.span_id == "c"
        assert e.parent_id == "p"
        assert e.from_entity == "from_e"
        assert e.to_entity == "to_e"
        assert e.edge_kind == "CHAIN_LLM"
        assert isinstance(e.edge_seq, int)
        # Timing is the joined child span's, not a denormalized edge column.
        assert e.started_at == started
        assert e.ended_at == ended

    def test_orphan_edge_has_null_from_entity(
        self, configured_db, insert_entity, insert_edge, insert_span
    ) -> None:
        insert_entity("to_e", semantic_kind="LLM")
        insert_span(trace_id="t", span_id="o", name="x", parent_id="missing")
        insert_edge(
            trace_id="t", span_id="o", parent_id="missing",
            from_entity=None, to_entity="to_e", edge_kind="UNKNOWN_LLM",
        )
        e = retrieval.get_edges().edges[0]
        assert e.from_entity is None
        assert e.edge_kind == "UNKNOWN_LLM"

    def test_trace_id_filter(
        self, configured_db, insert_entity, insert_edge, insert_span
    ) -> None:
        insert_entity("e", semantic_kind="UNKNOWN")
        for tr in ("t1", "t2"):
            insert_span(trace_id=tr, span_id="c", name="n", parent_id="p")
            insert_edge(trace_id=tr, span_id="c", parent_id="p", to_entity="e")

        only = retrieval.get_edges(trace_id="t1")
        assert {e.trace_id for e in only.edges} == {"t1"}


class TestGetEdgesKeyset:
    def test_cursor_on_edge_seq_skips_nothing_under_skewed_started_at(
        self, configured_db, insert_entity, insert_edge, insert_span
    ) -> None:
        """The keyset invariant: a full forward walk paginated on ``edge_seq``
        returns every edge exactly once, in ``edge_seq`` order, even when the
        child spans' ``started_at`` is the reverse of insertion order (the #30
        skew that broke the trace-clock cursors)."""
        insert_entity("e", semantic_kind="UNKNOWN")
        # Insert edges in order a, b, c (edge_seq increases), but give their
        # child spans DECREASING started_at — so started_at disagrees with
        # edge_seq.
        for i, sid in enumerate(["a", "b", "c"]):
            insert_span(
                trace_id="t", span_id=sid, name="n", parent_id="p",
                started_at=_T0 - dt.timedelta(minutes=i),
            )
            insert_edge(trace_id="t", span_id=sid, parent_id="p", to_entity="e")

        # Walk one row per page, cursoring on edge_seq.
        seen: list[str] = []
        seqs: list[int] = []
        cursor: int | None = None
        while True:
            page = retrieval.get_edges(cursor=cursor, limit=1)
            if not page.edges:
                break
            e = page.edges[0]
            seen.append(e.span_id)
            seqs.append(e.edge_seq)
            cursor = e.edge_seq

        # Every edge appears exactly once, ordered by edge_seq (== insert order).
        assert seen == ["a", "b", "c"]
        assert seqs == sorted(seqs)

    def test_order_desc(
        self, configured_db, insert_entity, insert_edge, insert_span
    ) -> None:
        insert_entity("e", semantic_kind="UNKNOWN")
        for sid in ["a", "b", "c"]:
            insert_span(trace_id="t", span_id=sid, name="n", parent_id="p")
            insert_edge(trace_id="t", span_id=sid, parent_id="p", to_entity="e")

        desc = retrieval.get_edges(order="desc")
        seqs = [e.edge_seq for e in desc.edges]
        assert seqs == sorted(seqs, reverse=True)


class TestGetEdgesValidation:
    def test_limit_over_cap_raises(self, configured_db) -> None:
        with pytest.raises(ValueError, match="hard cap"):
            retrieval.get_edges(limit=501)

    def test_bad_order_raises(self, configured_db) -> None:
        with pytest.raises(ValueError, match="order must be"):
            retrieval.get_edges(order="sideways")  # type: ignore[arg-type]
