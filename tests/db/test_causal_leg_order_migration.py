"""Migration 0026 replays persisted lineage in causal trace order."""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest
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


def _seed_one_llm_span_at_0022(conn, *, started_at, ended_at) -> None:
    conn.execute(
        "INSERT INTO entities (id, kind, natural_key, display_name, "
        "detected_from, seq, original_seq) VALUES "
        "('agent', 'agent', 'agent:a', 'a', 'inferred', 1, 1), "
        "('llm', 'llm', 'llm:m', 'm', 'inferred', 2, 2), "
        "('tool', 'tool', 'tool:t', 't', 'inferred', 3, 3)"
    )
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, kind, name, started_at, "
        "ended_at, arrival_seq, attributes, scope) VALUES "
        "('trace', 'llm-span', 'INTERNAL', 'messages.create', %s, %s, 1, "
        "'{\"openinference.span.kind\": \"LLM\", \"llm.model_name\": \"m\"}'::jsonb, "
        "'{\"name\": \"openinference.instrumentation.anthropic\"}'::jsonb)",
        (started_at, ended_at),
    )


def _insert_legs(conn, rows) -> None:
    # An inferred tool's synthetic response carries its LLM's completion body,
    # which is how the repair recognises a response it may move.
    for interaction_id, request_seq, request_time, response_time, payload, kind in rows:
        conn.execute(
            "INSERT INTO interaction_legs (interaction_id, leg_type, "
            "occurred_at, payload_hash, content_kind, error, seq) VALUES "
            "(%s, 'request', %s, %s, %s, false, %s), "
            "(%s, 'response', %s, %s, NULL, false, %s)",
            (
                interaction_id, request_time, payload, kind, request_seq,
                interaction_id, response_time, "completion", request_seq + 1,
            ),
        )


def test_inferred_tool_repair_uses_the_llm_call_that_owns_the_span(
    pg_dsn: str, monkeypatch,
) -> None:
    """Several LLM interactions can name one LLM span: the call holding the
    span's own anchor row, and calls on synthetic ``span#llm:...#N`` rows (a
    second co-anchored call, or one left by an earlier derivation of the
    trace). Their legs can place the tool on opposite sides of the exchange.
    The repair compares the tool with the call that owns the span's anchor
    row, which is unique per span, so it has one defined outcome.
    """
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0022_content_kind_on_legs")

    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = start + dt.timedelta(seconds=5)
    with psycopg.connect(pg_dsn) as conn:
        _seed_one_llm_span_at_0022(conn, started_at=start, ended_at=end)
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES ('prompt', '{}'::jsonb, 2), ('completion', '{}'::jsonb, 2), "
            "('args', '{}'::jsonb, 2)"
        )
        # The synthetic-anchor call is written first so a scan meets it first.
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES "
            "('co-anchored-llm', 'trace', 'agent', 'llm', 'call'), "
            "('llm-ix', 'trace', 'agent', 'llm', 'call'), "
            "('tool-ix', 'trace', 'agent', 'tool', 'call')"
        )
        conn.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES "
            "('co-anchored-llm', 'trace', 'llm-span#llm:m#20', 'anchor', 'request'), "
            "('llm-ix', 'trace', 'llm-span', 'anchor', 'request'), "
            "('tool-ix', 'trace', 'llm-span#tool:t#12', 'anchor', 'request')"
        )
        _insert_legs(conn, [
            # Against this call the tool follows the LLM response: output.
            ("llm-ix", 10, start, end, "prompt", "llm_chat_prompt"),
            # Against this one it precedes the LLM request: an input replay.
            ("co-anchored-llm", 20, start, end, "prompt", "llm_chat_prompt"),
            ("tool-ix", 12, start, end, "args", "tool_call_arguments"),
        ])

    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute(
            "SELECT leg_type::text, occurred_at FROM interaction_legs "
            "WHERE interaction_id = 'tool-ix' ORDER BY leg_type"
        ).fetchall()
    assert rows == [("request", end), ("response", end)]


def test_unended_llm_exchange_is_repaired_before_lineage_replay(
    pg_dsn: str, monkeypatch,
) -> None:
    """Repair the LLM response too, so its output call inherits the LLM hop.
    The lineage cursor has already passed these legs; the migration queue is
    what must repair their persisted provenance."""
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0022_content_kind_on_legs")

    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    with psycopg.connect(pg_dsn) as conn:
        _seed_one_llm_span_at_0022(conn, started_at=start, ended_at=None)
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES ('prompt', '{}'::jsonb, 2), ('completion', '{}'::jsonb, 2), "
            "('args', '{}'::jsonb, 2)"
        )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES "
            "('llm-ix', 'trace', 'agent', 'llm', 'call'), "
            "('tool-ix', 'trace', 'agent', 'tool', 'call')"
        )
        conn.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES "
            "('llm-ix', 'trace', 'llm-span', 'anchor', 'request'), "
            "('tool-ix', 'trace', 'llm-span#tool:t#12', 'anchor', 'request')"
        )
        # The old projection copied the span's start and its missing end.
        _insert_legs(conn, [
            ("llm-ix", 10, start, None, "prompt", "llm_chat_prompt"),
            ("tool-ix", 12, start, None, "args", "tool_call_arguments"),
        ])
        conn.execute(
            "INSERT INTO processor_state (processor_name, last_processed_seq, updated_at) "
            "VALUES ('data_lineage', 13, now())"
        )

    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute(
            "SELECT interaction_id, leg_type::text, occurred_at, seq "
            "FROM interaction_legs ORDER BY seq"
        ).fetchall()
    assert rows == [
        ("llm-ix", "request", start, 10),
        ("llm-ix", "response", start, 11),
        ("tool-ix", "request", start, 12),
        ("tool-ix", "response", start, 13),
    ]

    db.close_pool()
    db.configure(pg_dsn)
    try:
        assert driver.drain(13) == 13
        result = retrieval.get_data_lineage("trace")
        assert result.status == "complete"
        assert [(leg.interaction_id, leg.leg_type) for leg in result.legs] == [
            ("llm-ix", "request"), ("llm-ix", "response"),
            ("tool-ix", "request"), ("tool-ix", "response"),
        ]
        tool_request = result.legs[2]
        assert tool_request.lineage is not None
        assert tool_request.lineage.entities == ["agent:a", "llm:m"]
    finally:
        db.close_pool()


@pytest.mark.parametrize("scenario", [
    "enclosing_call", "observed_response", "request_only", "ended_span",
    "transport_scope", "non_llm_span",
])
def test_llm_response_backfill_preserves_observed_and_incomplete_calls(
    pg_dsn: str, monkeypatch, scenario: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0022_content_kind_on_legs")

    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = start + dt.timedelta(seconds=5)
    response_at = end if scenario == "observed_response" else None
    with psycopg.connect(pg_dsn) as conn:
        _seed_one_llm_span_at_0022(
            conn, started_at=start, ended_at=end if scenario == "ended_span" else None,
        )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES "
            "('llm-ix', 'trace', 'agent', 'llm', 'call')"
        )
        conn.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES "
            "('llm-ix', 'trace', 'llm-span', 'anchor', 'request')"
        )
        _insert_legs(conn, [("llm-ix", 10, start, response_at, "prompt", "llm_chat_prompt")])
        if scenario == "enclosing_call":
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, "
                "callee_entity_id, summary, parent_interaction_id) VALUES "
                "('child', 'trace', 'agent', 'tool', 'call', 'llm-ix')"
            )
        elif scenario == "request_only":
            conn.execute(
                "DELETE FROM interaction_legs "
                "WHERE interaction_id = 'llm-ix' AND leg_type = 'response'"
            )
        elif scenario == "transport_scope":
            conn.execute(
                "UPDATE spans SET scope = '{\"name\": \"opentelemetry.instrumentation.httpx\"}'::jsonb"
            )
        elif scenario == "non_llm_span":
            conn.execute(
                "UPDATE spans SET attributes = '{\"openinference.span.kind\": \"TOOL\"}'::jsonb"
            )

    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        response = conn.execute(
            "SELECT occurred_at, seq FROM interaction_legs "
            "WHERE interaction_id = 'llm-ix' AND leg_type = 'response'"
        ).fetchone()
    assert response == (None if scenario == "request_only" else (response_at, 11))


def test_inferred_tool_repair_reads_the_leg_content_kind_after_0022(
    pg_dsn: str, monkeypatch,
) -> None:
    """On a database already at 0022 the content kind lives on the leg (issue
    #286), so the repair reads ``interaction_legs.content_kind``. A kinded leg is
    trusted: ``tool_call_arguments`` is projected even when its body equals the
    LLM's (bodies are content-addressed), and any other kind is never moved.
    """
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    cfg = _alembic_config()
    command.upgrade(cfg, "0022_content_kind_on_legs")

    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = start + dt.timedelta(seconds=5)
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
            "('trace', 'llm-span', 'INTERNAL', 'messages.create', %s, %s, 1)",
            (start, end),
        )
        conn.execute(
            "INSERT INTO interaction_payloads (content_hash, content, byte_size) "
            "VALUES ('prompt', '{}'::jsonb, 2), ('completion', '{}'::jsonb, 2)"
        )
        conn.execute(
            "INSERT INTO interactions (id, trace_id, caller_entity_id, "
            "callee_entity_id, summary) VALUES "
            "('llm-ix', 'trace', 'agent', 'llm', 'call'), "
            "('kinded-tool', 'trace', 'agent', 'tool', 'call'), "
            "('other-kind', 'trace', 'agent', 'tool', 'call')"
        )
        conn.execute(
            "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
            "role, leg_type) VALUES "
            "('llm-ix', 'trace', 'llm-span', 'anchor', 'request'), "
            "('kinded-tool', 'trace', 'llm-span#tool:t#12', 'anchor', 'request'), "
            "('other-kind', 'trace', 'llm-span#tool:t#14', 'anchor', 'request')"
        )
        for interaction_id, request_seq, payload, kind in (
            ("llm-ix", 10, "prompt", "llm_chat_prompt"),
            # The arguments body equals the completion's: only the kind says
            # this is a tool call's arguments.
            ("kinded-tool", 12, "completion", "tool_call_arguments"),
            ("other-kind", 14, "completion", "llm_completion"),
        ):
            response_payload = "completion" if interaction_id == "llm-ix" else None
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, "
                "occurred_at, payload_hash, content_kind, error, seq) VALUES "
                "(%s, 'request', %s, %s, %s, false, %s), "
                "(%s, 'response', %s, %s, NULL, false, %s)",
                (
                    interaction_id, start, payload, kind, request_seq,
                    interaction_id, end, response_payload, request_seq + 1,
                ),
            )

    command.upgrade(cfg, "head")
    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute(
            "SELECT interaction_id, leg_type::text, occurred_at "
            "FROM interaction_legs WHERE interaction_id IN "
            "('kinded-tool', 'other-kind') AND leg_type = 'request' "
            "ORDER BY interaction_id"
        ).fetchall()
    assert rows == [
        ("kinded-tool", "request", end),
        ("other-kind", "request", start),
    ]
