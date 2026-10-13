"""The streaming algorithm stamps each leg with its own extraction's content
kind and keeps it across the per-span re-derivation (issue #286).

Two things the sidecar tests cannot show: (1) a tool whose result echoes its
arguments makes ONE payload row referenced by a ``tool_call_arguments`` leg
and a ``tool_call_result`` leg — each keeps its own kind; (2) the rehydrate
half of the flush boundary reads the kinds back, so the flush a later span
triggers re-stamps the same values instead of NULL.
"""

from __future__ import annotations

import psycopg

from data_governance.processors.interactions.driver import drain

from .conftest import _insert_spans, _load_fixture_spans

ECHO = "e3f040e0f5f7d471"  # travel-advisor search_destinations TOOL span


def _legs(dsn: str, anchor_span_id: str) -> list[tuple[str, str | None, str | None]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT l.leg_type::text, l.payload_hash, l.content_kind "
            "FROM interaction_legs l "
            "JOIN interaction_spans s ON s.interaction_id = l.interaction_id "
            "WHERE s.span_id = %s AND s.role = 'anchor' ORDER BY l.leg_type",
            (anchor_span_id,),
        ).fetchall()


def test_echo_tool_legs_share_one_row_and_keep_their_own_kinds(configured_db: str) -> None:
    spans = _load_fixture_spans()
    (tool,) = [s for s in spans if s["span_id"] == ECHO]
    tool["attributes"]["output.value"] = tool["attributes"]["input.value"]
    _insert_spans(configured_db, spans)
    drain(0)

    legs = _legs(configured_db, ECHO)
    assert [t for t, _h, _k in legs] == ["request", "response"]
    (_, req_hash, req_kind), (_, resp_hash, resp_kind) = legs
    assert req_hash == resp_hash is not None
    assert (req_kind, resp_kind) == ("tool_call_arguments", "tool_call_result")
    with psycopg.connect(configured_db) as conn:
        (rows,) = conn.execute(
            "SELECT count(*) FROM interaction_payloads WHERE content_hash = %s", (req_hash,)
        ).fetchone()
    assert rows == 1


def test_kinds_survive_the_flushes_later_spans_trigger(configured_db: str) -> None:
    """Every bodied leg of the whole fixture still carries a kind after the full
    drain — the later spans' flushes rehydrate the interaction with its kinds
    rather than overwriting them with NULL."""
    _insert_spans(configured_db, _load_fixture_spans())
    drain(0)
    with psycopg.connect(configured_db) as conn:
        bodied, with_kind = conn.execute(
            "SELECT count(*), count(content_kind) FROM interaction_legs "
            "WHERE payload_hash IS NOT NULL"
        ).fetchone()
    assert bodied > 0
    assert with_kind == bodied
