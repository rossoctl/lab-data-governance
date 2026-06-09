"""Real-data validation of the graph-builder (not synthetic).

Seeds one genuine captured trace (``fixtures/real_trace.json`` — a payment-agent
flow pulled from the live span store) and asserts the backfill derives the
expected entities and edges. This is the check synthetic tests cannot make: it
proves the ``semantic_kind`` ladder fires on the attribute keys the kagenti
collector *actually* leaves on a span post-transform, and that the
``(service, semantic_kind, sub_kind)`` grain yields the intended real nodes
(distinct tools and models not collapsing).

Ground truth (computed from the fixture): 9 entities, 28 edges, 1 real root,
0 orphans, 44 same-entity internal pairs suppressed.
"""

from __future__ import annotations

import json
import pathlib

import psycopg

from data_governance.processors.graph_builder import entity_id, run_backfill


_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "real_trace.json"
_SVC_PAY = "agent-examples-snp-payment-agent"


def _load_fixture() -> list[dict]:
    return json.loads(_FIXTURE.read_text())


class TestBackfillRealTrace:
    def test_counts_match_ground_truth(self, configured_db: str, seed_spans) -> None:
        seed_spans(_load_fixture())
        result = run_backfill()

        assert result.spans_scanned == 73
        assert result.entities_upserted == 9
        assert result.edges_upserted == 28
        assert result.orphan_edges == 0  # this captured trace is complete

    def test_real_entities_have_expected_grain(
        self, configured_db: str, seed_spans
    ) -> None:
        seed_spans(_load_fixture())
        run_backfill()

        with psycopg.connect(configured_db) as conn:
            ids = {r[0] for r in conn.execute("SELECT entity_id FROM entities").fetchall()}

        # The LLM model node — proves the llm.* / openinference ladder fired on
        # real keys rather than dropping to UNKNOWN.
        assert entity_id(_SVC_PAY, "LLM", "claude-haiku-4-5-20251001") in ids
        # Two distinct tools stay distinct (sub_kind prevents collapse).
        assert entity_id(_SVC_PAY, "TOOL", "charge_card") in ids
        assert entity_id(_SVC_PAY, "TOOL", "get_payment_info") in ids
        # Agent and chain nodes.
        assert entity_id(_SVC_PAY, "AGENT", None) in ids
        assert entity_id(_SVC_PAY, "CHAIN", None) in ids

    def test_real_edge_kinds_present(self, configured_db: str, seed_spans) -> None:
        seed_spans(_load_fixture())
        run_backfill()

        with psycopg.connect(configured_db) as conn:
            kinds = {
                r[0] for r in conn.execute("SELECT DISTINCT edge_kind FROM edges").fetchall()
            }
        # Genuine agent lineage boundaries.
        for expected in ("CHAIN_LLM", "CHAIN_TOOL", "CLIENT_SERVER", "AGENT_CHAIN"):
            assert expected in kinds, f"missing real edge kind {expected}"

    def test_no_self_loops(self, configured_db: str, seed_spans) -> None:
        seed_spans(_load_fixture())
        run_backfill()
        # Same-entity boundaries must never be materialized as edges.
        with psycopg.connect(configured_db) as conn:
            n = conn.execute(
                "SELECT count(*) FROM edges WHERE from_entity = to_entity"
            ).fetchone()
        assert n == (0,)

    def test_rerun_is_a_no_op(self, configured_db: str, seed_spans) -> None:
        seed_spans(_load_fixture())
        run_backfill()
        with psycopg.connect(configured_db) as conn:
            e1 = conn.execute("SELECT count(*) FROM entities").fetchone()
            g1 = conn.execute(
                "SELECT span_id, from_entity, to_entity, edge_kind, edge_seq FROM edges "
                "ORDER BY span_id"
            ).fetchall()

        run_backfill()
        with psycopg.connect(configured_db) as conn:
            e2 = conn.execute("SELECT count(*) FROM entities").fetchone()
            g2 = conn.execute(
                "SELECT span_id, from_entity, to_entity, edge_kind, edge_seq FROM edges "
                "ORDER BY span_id"
            ).fetchall()

        assert e1 == e2
        assert g1 == g2  # identical rows incl. edge_seq
