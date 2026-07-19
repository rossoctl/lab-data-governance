"""The ``--scramble`` order-independence gate (#72).

Carries the prototype's ``--scramble`` torture test forward as a CI regression
guard against the production ``processors.interactions`` processor. The proto
had no automated tests; this slice pins the verified order-independence
property so a future refactor of the ported classifier cannot silently
re-introduce arrival-order divergence.

The driver drains the ``spans`` table by ``seq`` (``WHERE seq > cursor ORDER BY
seq``), so changing the order spans are *processed* in requires re-assigning
seqs, not merely shuffling INSERT order (that would be a no-op — the seq
horizon would replay the same order). The ``scrambled_trace`` fixture
re-sequences the 281 spans in reversed-seq order so every parent arrives after
its children, exercising the late-parent re-derive path — the production
analogue of the prototype's ``extract(scramble_for_late_parent=True)``.

This pins order-independence at two layers:

CLASSIFIER (fixed during the grilling session, memory
``proto-b-fails-on-new-281-trace``):
  1. external-http bare ``POST /`` A2A detection — a CLIENT egress to bare ``/``
     is an A2A agent call (``create_booking → payment-agent`` cross-service),
     not external-http. The negative "no SERVER child ⟹ external" conclusion was
     arrival-order-dependent; the positive URL-shape gate is not.
  2. frontier-only tool-transport-signal walk — ``delegate_to_booking_agent``
     stays an in-process TOOL, not a deployed one, regardless of when the
     sub-agent's own ``/mcp`` SERVERs arrive.

STATE LAYER (fixed in this slice — exposed by re-sequencing, which the proto's
accumulate-then-``result()``-once model could not surface):
  3. rehydrate primary-anchor recovery — a cross-service edge has TWO ``anchor``
     rows (CLIENT egress + callee SERVER); rehydrate must key the interaction on
     its TRUE primary (the id-seeding span), not an arbitrary one, or
     ``_innermost_owner_for`` misses it and detaches its territory.
  4. ``_LazyChildren`` ancestor over-marking — the rehydrate pre-seed must not
     mark an ancestor's subtree "materialised", or a late-emitting orphan-server
     edge (root anchor) walks an empty bucket and drops its territory spans.

The byte gate covers the four GRAPH tables (entities, interactions,
interaction_spans, payloads). entity_spans is asserted on total count only — its
discovered_via/identified_via split is provenance, not graph, and is not
arrival-independent in this slice (see the note in ``state.flush``).
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from .conftest import (
    EXPECTED_ENTITIES,
    EXPECTED_INTERACTIONS,
    EXPECTED_INTERACTION_SPAN_ROLES,
    EXPECTED_PAYLOADS,
    drain_all as _drain_all,
    load_drain_snapshot,
)


def _role_counts(dsn: str, table: str) -> dict[str, int]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            f"SELECT role, count(*) FROM {table} GROUP BY role"
        ).fetchall()
    return {role: n for role, n in rows}


# The graph tables whose every row must be byte-identical across arrival
# orders. entity_spans is excluded on PURPOSE: its discovered_via/identified_via
# split is provenance only (it touches none of the graph) and is not
# arrival-independent in this slice — see the note in state.flush. The scramble
# gate guards the graph; entity_spans is checked on total count below.
_GRAPH_TABLES = ("entities", "interactions", "interaction_spans", "payloads")


def test_scramble_derives_identical_graph(
    make_migrated_db: Callable[[], str],
) -> None:
    """The gate: in-order vs scrambled-arrival drains produce a byte-identical
    derived graph (entities, interactions, interaction_spans, payloads).

    Each arrival order drains its OWN fresh migrated DB (the ``make_migrated_db``
    factory — ``loaded_trace``/``scrambled_trace`` would share one
    function-scoped DB), then the two derived snapshots are compared. Children
    arriving before parents must not change a single graph row.
    """
    in_order = load_drain_snapshot(make_migrated_db(), scramble=False)
    scrambled = load_drain_snapshot(make_migrated_db(), scramble=True)

    # Compare table-by-table so a divergence names the offending table.
    for table in _GRAPH_TABLES:
        assert scrambled[table] == in_order[table], f"diverged: {table}"
    assert {t: scrambled[t] for t in _GRAPH_TABLES} == {
        t: in_order[t] for t in _GRAPH_TABLES
    }


def test_scramble_entity_spans_total_is_stable(
    make_migrated_db: Callable[[], str],
) -> None:
    """entity_spans total row count is arrival-independent (one row per
    entity/span reference). Only the provenance ROLE split within that total is
    order-sensitive, which the graph gate above deliberately does not pin."""
    in_order = load_drain_snapshot(make_migrated_db(), scramble=False)
    scrambled = load_drain_snapshot(make_migrated_db(), scramble=True)
    assert len(scrambled["entity_spans"]) == len(in_order["entity_spans"])


def test_scramble_matches_verified_counts(scrambled_trace: str) -> None:
    """Under scrambled arrival the verified gate numbers still hold: 31
    interactions, 15 entities, 50 payloads (the scramble baseline is the
    correct one)."""
    _drain_all(scrambled_trace)
    with psycopg.connect(scrambled_trace) as conn:
        def n(table: str) -> int:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        assert n("interactions") == EXPECTED_INTERACTIONS
        assert n("entities") == EXPECTED_ENTITIES
        assert n("interaction_payloads") == EXPECTED_PAYLOADS


def test_scramble_interaction_span_role_split(scrambled_trace: str) -> None:
    """The interaction_spans role split — the graph dimension that DIVERGED by
    arrival order before the fixes — matches the captured scramble baseline:
    35 anchor / 66 connector / 100 info. (entity_spans roles are provenance and
    intentionally not pinned; see test_scramble_derives_identical_graph.)"""
    _drain_all(scrambled_trace)
    assert _role_counts(scrambled_trace, "interaction_spans") == (
        EXPECTED_INTERACTION_SPAN_ROLES
    )


def test_scramble_psp_mock_is_only_service_entity(scrambled_trace: str) -> None:
    """Pins fix #1: under scrambled arrival, ``psp-mock`` (``/charge``) is still
    the ONLY ``service:`` entity — ``create_booking → payment-agent`` (bare
    ``POST /``) resolves cross-service, NOT external-http, so no stray
    ``service:payment-agent`` is minted."""
    _drain_all(scrambled_trace)
    with psycopg.connect(scrambled_trace) as conn:
        rows = conn.execute(
            "SELECT natural_key FROM entities WHERE kind = 'service' "
            "ORDER BY natural_key"
        ).fetchall()
    assert [r[0] for r in rows] == ["service:psp-mock"], [r[0] for r in rows]


def test_scramble_create_booking_to_payment_agent_is_cross_service(
    scrambled_trace: str,
) -> None:
    """Pins fix #1 at the interaction level: the ``create_booking`` tool's call
    out to ``payment-agent`` is a cross-service agent interaction (caller is the
    ``create_booking`` deployed-tool entity, callee is the ``payment-agent``
    agent entity) under scrambled arrival.

    Entities are keyed by composite ``natural_key`` (``tool:(<proj>,<svc>)`` /
    ``agent:(<proj>,<svc>)``), so we match on the stable ``display_name`` +
    ``kind`` shown in the captured baseline rather than the exact key string.
    """
    _drain_all(scrambled_trace)
    with psycopg.connect(scrambled_trace) as conn:
        rows = conn.execute(
            """
            SELECT caller.display_name, caller.kind, callee.display_name, callee.kind
            FROM interactions i
            JOIN entities caller ON caller.id = i.caller_entity_id
            JOIN entities callee ON callee.id = i.callee_entity_id
            WHERE caller.display_name = 'create_booking'
              AND callee.display_name = 'payment-agent'
            """
        ).fetchall()
    # Exactly one create_booking → payment-agent interaction; the caller is the
    # deployed tool, the callee is the A2A agent (cross-service, not external).
    assert rows == [("create_booking", "tool", "payment-agent", "agent")], rows


def test_scramble_delegate_tool_is_caller_of_its_a2a_leg(
    scrambled_trace: str,
) -> None:
    """Pins ADR-0016 (refining ADR-0010): the in-framework delegate's outbound
    A2A leg is attributed to the delegate TOOL, not the enclosing agent. The
    A2A CLIENT POST is a child of the in-process ``delegate_to_*`` TOOL span, so
    ``_resolve_cross_service_caller`` resolves the caller to that tool — exactly
    as ``create_booking → payment-agent`` (the deployed case) already does.

    Under scrambled arrival the caller must STILL be the tool (the resolution is
    structural — ``_classify_oi_endpoint`` on the enclosing TOOL span — so it
    does not depend on whether the tool's own ``agent→tool`` edge emitted
    first). We match on the stable ``display_name`` + ``kind``.
    """
    _drain_all(scrambled_trace)
    # Each delegate primitive's A2A leg(s) into its sub-agent are caller = the
    # in-process delegate tool (kind 'tool'), NOT the travel-advisor agent.
    for tool_name, sub_agent in (
        ("delegate_to_booking_agent", "booking-agent"),
        ("delegate_to_research_agent", "research-agent"),
    ):
        with psycopg.connect(scrambled_trace) as conn:
            rows = conn.execute(
                """
                SELECT caller.display_name, caller.kind,
                       callee.display_name, callee.kind
                FROM interactions i
                JOIN entities caller ON caller.id = i.caller_entity_id
                JOIN entities callee ON callee.id = i.callee_entity_id
                WHERE callee.display_name = %s AND callee.kind = 'agent'
                  AND caller.kind = 'tool'
                ORDER BY i.seq
                """,
                (sub_agent,),
            ).fetchall()
        assert rows and all(
            r == (tool_name, "tool", sub_agent, "agent") for r in rows
        ), (tool_name, rows)
        # And there is NO agent→<sub-agent> A2A leg left (the flip is complete).
        with psycopg.connect(scrambled_trace) as conn:
            stray = conn.execute(
                """
                SELECT count(*)
                FROM interactions i
                JOIN entities caller ON caller.id = i.caller_entity_id
                JOIN entities callee ON callee.id = i.callee_entity_id
                WHERE callee.display_name = %s AND callee.kind = 'agent'
                  AND caller.kind = 'agent'
                """,
                (sub_agent,),
            ).fetchone()[0]
        assert stray == 0, f"unexpected agent→{sub_agent} A2A legs: {stray}"


def test_scramble_delegate_to_booking_agent_is_in_process_tool(
    scrambled_trace: str,
) -> None:
    """Pins fix #2: ``delegate_to_booking_agent`` is an in-process TOOL entity
    (not a deployed/service one) regardless of when the sub-agent's own ``/mcp``
    SERVER spans arrive. The in-process detection is what distinguishes it from
    the deployed tools, so we assert both ``kind`` and ``detected_from``."""
    _drain_all(scrambled_trace)
    with psycopg.connect(scrambled_trace) as conn:
        rows = conn.execute(
            "SELECT kind, detected_from FROM entities "
            "WHERE display_name = 'delegate_to_booking_agent'"
        ).fetchall()
    assert rows == [("tool", "OpenInference TOOL span (in-process)")], rows
