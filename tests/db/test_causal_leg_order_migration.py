"""Migration 0021 replays persisted lineage in causal trace order."""

from __future__ import annotations

import datetime as dt

import psycopg
from alembic import command

from data_governance import db, retrieval
from data_governance.db.migrate import _alembic_config
from data_governance.processors.data_lineage import driver


def test_existing_lineage_is_invalidated_and_replayed_from_the_dirty_queue(
    pg_dsn: str, monkeypatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0020_entity_namespace")

    with psycopg.connect(pg_dsn) as conn:
        for eid, kind, natural_key in (
            ("client", "client", "client:traveler"),
            ("agent", "agent", "agent:advisor"),
            ("tool", "tool", "tool:weather"),
        ):
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, "
                "detected_from, seq, original_seq) "
                "VALUES (%s, %s, %s, %s, 'observed', 1, 1)",
                (eid, kind, natural_key, natural_key),
            )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary, parent_interaction_id) "
            "VALUES ('child', 'trace', 'agent', 'tool', 'call', 'parent'), "
            "('parent', 'trace', 'client', 'agent', 'call', NULL)"
        )
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, occurred_at, "
            "payload_hash, error) VALUES "
            "('child', 'request', '2026-01-01T00:00:01Z', 'child-body', false), "
            "('parent', 'request', '2026-01-01T00:00:10Z', 'parent-body', false)"
        )
        seqs = dict(conn.execute(
            "SELECT interaction_id, seq FROM interaction_legs"
        ).fetchall())
        assert seqs["child"] < seqs["parent"]
        for iid, source in (("child", "agent:advisor"), ("parent", "client:traveler")):
            conn.execute(
                "INSERT INTO lineage_metadata (interaction_id, leg_type, data_sources, "
                "source_transformations, entities, payload_hash, seq) "
                "VALUES (%s, 'request', %s, '{}'::jsonb, '{}'::text[], %s, %s)",
                (iid, [source], f"{iid}-body", seqs[iid]),
            )
        conn.execute(
            "INSERT INTO lineage_trace_status (trace_id, status, stopped_at_seq) "
            "VALUES ('trace', 'complete', NULL)"
        )
        conn.execute(
            "INSERT INTO processor_state (processor_name, last_processed_seq, updated_at) "
            "VALUES ('data_lineage', %s, now())",
            (seqs["parent"],),
        )
        conn.commit()

    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM lineage_metadata").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM lineage_trace_status").fetchone() == (0,)
        assert conn.execute(
            "SELECT trace_id FROM data_lineage_dirty_traces"
        ).fetchall() == [("trace",)]
        assert conn.execute(
            "SELECT last_processed_seq FROM processor_state "
            "WHERE processor_name = 'data_lineage'"
        ).fetchone() == (seqs["parent"],)

    db.close_pool()
    db.configure(pg_dsn)
    try:
        assert driver.drain(seqs["parent"]) == seqs["parent"]
        result = retrieval.get_data_lineage("trace")
        assert result.status == "complete"
        assert [leg.interaction_id for leg in result.legs] == ["parent", "child"]
        assert result.legs[1].lineage is not None
        assert result.legs[1].lineage.data_sources == ["client:traveler"]
    finally:
        db.close_pool()


def test_legacy_inferred_tool_times_are_projected_before_lineage_replay(
    pg_dsn: str, monkeypatch,
) -> None:
    """Old graph rows copied one LLM span's start/end onto inferred tool legs.
    Output calls are known at the LLM response; input replays at its request.
    Correcting those stored times is necessary before replaying lineage.
    """
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0020_entity_namespace")

    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = start + dt.timedelta(seconds=5)
    next_start = end + dt.timedelta(seconds=1)
    next_end = next_start + dt.timedelta(seconds=5)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "INSERT INTO entities (id, kind, natural_key, display_name, "
            "detected_from, seq, original_seq) VALUES "
            "('agent', 'agent', 'agent:a', 'a', 'inferred', 1, 1), "
            "('llm', 'llm', 'llm:m', 'm', 'inferred', 2, 2), "
            "('tool', 'tool', 'tool:t', 't', 'inferred', 3, 3)"
        )
        conn.execute(
            "INSERT INTO spans (trace_id, span_id, kind, name, started_at, "
            "ended_at, arrival_seq) VALUES "
            "('trace', 'output-span', 'INTERNAL', 'messages.create', %s, %s, 1), "
            "('trace', 'input-span', 'INTERNAL', 'messages.create', %s, %s, 2)",
            (start, end, next_start, next_end),
        )
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content_kind, "
            "content, byte_size) VALUES "
            "('out-args', 'tool_call_arguments', '{}'::jsonb, 2), "
            "('in-args', 'tool_call_arguments', '{}'::jsonb, 2), "
            "('observed-result', 'tool_result', '{}'::jsonb, 2)"
        )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES "
            "('out-llm', 'trace', 'agent', 'llm', 'call'), "
            "('out-tool', 'trace', 'agent', 'tool', 'call'), "
            "('observed-tool', 'trace', 'agent', 'tool', 'call'), "
            "('in-llm', 'trace', 'agent', 'llm', 'call'), "
            "('in-tool', 'trace', 'agent', 'tool', 'call')"
        )
        conn.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES "
            "('out-llm', 'trace', 'output-span', 'anchor', 'request'), "
            "('out-tool', 'trace', 'output-span#tool:t#12', 'anchor', 'request'), "
            "('observed-tool', 'trace', 'output-span#tool:t#14', 'anchor', 'request'), "
            "('in-llm', 'trace', 'input-span', 'anchor', 'request'), "
            "('in-tool', 'trace', 'input-span#tool:t#20', 'anchor', 'request')"
        )
        for interaction_id, request_seq, request_time, response_time, payload, response_payload in (
            ("out-llm", 10, start, end, None, None),
            ("out-tool", 12, start, end, "out-args", None),
            ("observed-tool", 14, start, next_start, "out-args", "observed-result"),
            ("in-tool", 20, next_start, next_end, "in-args", None),
            ("in-llm", 22, next_start, next_end, None, None),
        ):
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, "
                "occurred_at, payload_hash, error, seq) VALUES "
                "(%s, 'request', %s, %s, false, %s), "
                "(%s, 'response', %s, %s, false, %s)",
                (
                    interaction_id, request_time, payload, request_seq,
                    interaction_id, response_time, response_payload, request_seq + 1,
                ),
            )

    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute(
            "SELECT interaction_id, leg_type::text, occurred_at "
            "FROM interaction_legs WHERE interaction_id IN "
            "('out-tool', 'in-tool', 'observed-tool') "
            "ORDER BY interaction_id, leg_type"
        ).fetchall()
    assert rows == [
        ("in-tool", "request", next_start),
        ("in-tool", "response", next_start),
        ("observed-tool", "request", end),
        ("observed-tool", "response", next_start),
        ("out-tool", "request", end),
        ("out-tool", "response", end),
    ]
