"""The content kind lives on the leg, not on the shared payload row (issue #286).

The golden trace has the collision the issue describes: the agent returns its
LLM's final answer verbatim, so the entry's response leg (``agent_response``)
and the second LLM call's response leg (``llm_completion``) reference ONE
content-addressed payload row. Before migration 0022 that row carried the
first writer's kind and the other leg read it. Now each leg carries its own
kind, the row is bytes-only, and P-classification still writes exactly one
verdict per hash — the verdict is a function of the bytes alone.
"""

from __future__ import annotations

import psycopg

from data_governance.processors.classification import driver as classification
from data_governance.processors.interactions.procedure import _interaction_id
from data_governance.processors.interactions.sidecar_driver import drain

from . import sidecar_golden as golden

I1 = _interaction_id(golden.TRACE, golden.A1)  # alice → agent (a2a)
I2 = _interaction_id(golden.TRACE, golden.B1)  # agent → llm #1
I3 = _interaction_id(golden.TRACE, golden.B3)  # agent → tool (mcp)
I4 = _interaction_id(golden.TRACE, golden.B5)  # agent → llm #2


def _legs(dsn: str) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """(interaction_id, leg_type) -> (payload_hash, content_kind)."""
    with psycopg.connect(dsn) as conn:
        return {
            (iid, leg): (ph, ck)
            for iid, leg, ph, ck in conn.execute(
                "SELECT interaction_id, leg_type::text, payload_hash, content_kind "
                "FROM interaction_legs"
            ).fetchall()
        }


def test_shared_payload_row_keeps_each_legs_own_kind(configured_db: str) -> None:
    golden.insert_spans(configured_db)
    drain(0)

    legs = _legs(configured_db)
    entry_resp = legs[(I1, "response")]
    llm2_resp = legs[(I4, "response")]
    # One row, two references, two kinds.
    assert entry_resp[0] == llm2_resp[0] is not None
    assert entry_resp[1] == "agent_response"
    assert llm2_resp[1] == "llm_completion"
    # Every leg carries the kind its exchange's facts give it, body or not.
    assert legs[(I1, "request")][1] == "agent_request"
    assert legs[(I2, "request")][1] == "llm_chat_prompt"
    assert legs[(I2, "response")][1] == "llm_completion"
    assert legs[(I3, "request")][1] == "tool_call_arguments"
    assert legs[(I3, "response")][1] == "tool_call_result"
    assert legs[(I4, "request")][1] == "llm_chat_prompt"

    with psycopg.connect(configured_db) as conn:
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'interaction_payloads'"
            ).fetchall()
        }
        (payloads,) = conn.execute("SELECT count(*) FROM interaction_payloads").fetchone()
    assert "content_kind" not in cols  # the row is bytes-only
    assert payloads == 7  # 8 bodied legs, one shared hash


def test_shared_payload_is_classified_exactly_once(configured_db: str) -> None:
    golden.insert_spans(configured_db)
    drain(0)
    classification.drain(0)

    with psycopg.connect(configured_db) as conn:
        (payloads,) = conn.execute("SELECT count(*) FROM interaction_payloads").fetchone()
        (verdicts,) = conn.execute(
            "SELECT count(*) FROM payload_classifications"
        ).fetchone()
        (shared_hash,) = conn.execute(
            "SELECT payload_hash FROM interaction_legs WHERE interaction_id = %s "
            "AND leg_type = 'response'",
            (I1,),
        ).fetchone()
        (shared_verdicts,) = conn.execute(
            "SELECT count(*) FROM payload_classifications WHERE content_hash = %s",
            (shared_hash,),
        ).fetchone()
    assert verdicts == payloads == 7
    assert shared_verdicts == 1


def test_rederive_fills_kinds_on_legs_written_before_0022(configured_db: str) -> None:
    """Legs written before migration 0022 carry NULL (no backfill). The
    operational recovery is a re-derive, and the ON CONFLICT update carries
    ``content_kind``, so one re-drain fills them."""
    golden.insert_spans(configured_db)
    drain(0)
    expected = _legs(configured_db)
    with psycopg.connect(configured_db) as conn:
        conn.execute("UPDATE interaction_legs SET content_kind = NULL")
        conn.commit()
    assert all(ck is None for _ph, ck in _legs(configured_db).values())
    drain(0)
    assert _legs(configured_db) == expected
