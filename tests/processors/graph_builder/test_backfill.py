"""Backfill logic tests over controlled span seeds (testcontainers Postgres).

These exercise the precise structural cases that real data cannot guarantee on
demand — same-entity self-loop suppression, the orphan NULL boundary, the
orphan NULL->real flip with edge_seq preserved, and re-run-is-a-no-op
idempotency. The real-data ladder validation lives in
``test_backfill_real_data.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from data_governance.processors.graph_builder import entity_id, run_backfill


def _entities(dsn: str) -> dict[str, tuple[Any, ...]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT entity_id, service_name, semantic_kind, sub_kind, "
            "first_seen_at, last_seen_at FROM entities"
        ).fetchall()
    return {r[0]: r[1:] for r in rows}


def _edges(dsn: str) -> dict[str, dict[str, Any]]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT span_id, from_entity, to_entity, edge_kind, edge_seq, parent_id "
            "FROM edges"
        ).fetchall()
    return {
        r[0]: {
            "from_entity": r[1],
            "to_entity": r[2],
            "edge_kind": r[3],
            "edge_seq": r[4],
            "parent_id": r[5],
        }
        for r in rows
    }


def _four_cases(make_span: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """Seed covering the four canonical cases (plus a second model)."""
    return [
        # (i) CHAIN parent -> two LLM children with *different* models in svcX.
        make_span("chain", service="svcX", attrs={"openinference.span.kind": "CHAIN"}),
        make_span(
            "llm_gpt",
            parent="chain",
            service="svcX",
            attrs={"openinference.span.kind": "LLM", "llm.model_name": "gpt-4"},
        ),
        make_span(
            "llm_claude",
            parent="chain",
            service="svcX",
            attrs={"openinference.span.kind": "LLM", "llm.model_name": "claude-3"},
        ),
        # (ii) same-(service,kind,sub) INTERNAL parent/child pair -> no edge.
        make_span("intp", service="svcX", kind="INTERNAL"),
        make_span("intc", parent="intp", service="svcX", kind="INTERNAL"),
        # (iii) cross-service CLIENT -> SERVER.
        make_span("cli", service="svcA", kind="CLIENT"),
        make_span("srv", parent="cli", service="svcB", kind="SERVER"),
        # (iv) orphan: parent_id references an absent span.
        make_span(
            "orph",
            parent="ABSENT",
            service="svcA",
            kind="INTERNAL",
            attrs={"openinference.span.kind": "LLM", "llm.model_name": "gpt-4"},
        ),
    ]


class TestBackfillFourCases:
    def test_entities_distinct_models_do_not_collapse(
        self, configured_db: str, seed_spans, make_span
    ) -> None:
        seed_spans(_four_cases(make_span))
        run_backfill()

        ents = _entities(configured_db)
        # Both svcX LLM models are distinct nodes (sub_kind splits them).
        assert entity_id("svcX", "LLM", "gpt-4") in ents
        assert entity_id("svcX", "LLM", "claude-3") in ents
        # The full expected set.
        expected = {
            entity_id("svcX", "CHAIN", None),
            entity_id("svcX", "LLM", "gpt-4"),
            entity_id("svcX", "LLM", "claude-3"),
            entity_id("svcX", "UNKNOWN", None),
            entity_id("svcA", "CLIENT", None),
            entity_id("svcB", "SERVER", None),
            entity_id("svcA", "LLM", "gpt-4"),
        }
        assert set(ents) == expected

    def test_edges_and_self_loop_suppression(
        self, configured_db: str, seed_spans, make_span
    ) -> None:
        seed_spans(_four_cases(make_span))
        run_backfill()

        edges = _edges(configured_db)
        # Exactly the cross-entity boundaries; the INTERNAL pair produced none.
        assert set(edges) == {"llm_gpt", "llm_claude", "srv", "orph"}
        assert "intc" not in edges  # case (ii): same-entity -> suppressed

        assert edges["llm_gpt"]["edge_kind"] == "CHAIN_LLM"
        assert edges["llm_claude"]["edge_kind"] == "CHAIN_LLM"
        assert edges["srv"]["edge_kind"] == "CLIENT_SERVER"
        assert edges["srv"]["from_entity"] == entity_id("svcA", "CLIENT", None)
        assert edges["srv"]["to_entity"] == entity_id("svcB", "SERVER", None)

    def test_orphan_boundary_is_preserved_not_dropped(
        self, configured_db: str, seed_spans, make_span
    ) -> None:
        seed_spans(_four_cases(make_span))
        result = run_backfill()

        edges = _edges(configured_db)
        assert edges["orph"]["from_entity"] is None
        assert edges["orph"]["edge_kind"] == "UNKNOWN_LLM"
        assert edges["orph"]["to_entity"] == entity_id("svcA", "LLM", "gpt-4")
        assert result.orphan_edges == 1

    def test_rerun_is_a_no_op(
        self, configured_db: str, seed_spans, make_span
    ) -> None:
        seed_spans(_four_cases(make_span))
        run_backfill()
        ents_before = _entities(configured_db)
        edges_before = _edges(configured_db)

        run_backfill()  # second pass
        ents_after = _entities(configured_db)
        edges_after = _edges(configured_db)

        # Identical content, and edge_seq unchanged (the read cursor never skews).
        assert ents_after == ents_before
        assert edges_after == edges_before


class TestOrphanResolution:
    def test_late_parent_flips_from_entity_and_preserves_edge_seq(
        self, configured_db: str, seed_spans, make_span
    ) -> None:
        seed_spans(_four_cases(make_span))
        run_backfill()

        before = _edges(configured_db)["orph"]
        assert before["from_entity"] is None
        seq_before = before["edge_seq"]

        # The previously-absent parent arrives (an AGENT span in svcA).
        seed_spans(
            [
                make_span(
                    "ABSENT",
                    service="svcA",
                    attrs={"openinference.span.kind": "AGENT"},
                )
            ]
        )
        run_backfill()

        after = _edges(configured_db)["orph"]
        # from_entity flips NULL -> the real AGENT entity; edge_kind upgrades;
        # edge_seq is unchanged (stable cursor axis).
        assert after["from_entity"] == entity_id("svcA", "AGENT", None)
        assert after["edge_kind"] == "AGENT_LLM"
        assert after["edge_seq"] == seq_before
