"""Tests for the anchor-fact evidence → OPA input mapping (issue #163).

Three layers, mirroring the engine's own split:

- pure: destination categorisation (#178's wildcard hostname whitelist, plus
  the dotless-short-name structural rule),
  ``build_opa_input``'s new ``data_destinations`` / ``event_type`` /
  ``accessing_user`` mapping, and — the correctness trap the issue calls out
  explicitly — the anchor facts joining ``fingerprint`` so a changed
  destination re-triggers OPA;
- DB: ``gather_evidence`` reading the anchor (request) span's wire facts
  through ``interaction_spans.role = 'anchor'`` → ``spans.attributes``, plus
  the interaction's ``parent_interaction_id``;
- both honest-absence directions: a non-sidecar anchor (no ``lineage.*``
  attributes) and a missing anchor row yield ``anchor=None`` and an OPA
  input with none of the new fields.

Real DB via ``configured_db`` for the evidence layer (the repo never fakes
Postgres); the pure layer needs neither DB nor HTTP.
"""

from __future__ import annotations

import json

import psycopg

from data_governance.risk.engine import utils
from data_governance.risk.engine.evidence import gather_evidence

_PATTERNS = [
    "*.svc",
    "*.svc.cluster.local",
    "*.localtest.me",
    "localhost",
    "*.localhost",
    "127.0.0.1",
    "host.containers.internal",
]


# --- destination categorisation ----------------------------------------------
# The matcher itself is #178's ``matches_internal_whitelist``; what is tested
# here is how ``build_opa_input`` uses it — including the one case no glob can
# express (a hostname with no dot at all).


def _category(host: str) -> str:
    """The category ``build_opa_input`` would emit for an outbound call to
    *host*, exercised through the public surface rather than a private
    helper."""
    anchor = utils.AnchorFacts(direction="outbound", peer_host=host)
    payload = utils.build_opa_input(**_BASE, anchor=anchor, internal_patterns=_PATTERNS)
    (categories,) = [d["data_destination_categories"] for d in payload["data_destinations"]]
    (category,) = categories
    return category


def test_cluster_service_hosts_are_internal() -> None:
    for host in [
        "records-tool.team2.svc:8000",
        "records-tool.team2.svc.cluster.local",
        "dg.localtest.me:8080",
        "localhost:11434",
        "127.0.0.1",
        "host.containers.internal:11434",
    ]:
        assert _category(host) == "internal", host


def test_dotless_short_names_are_internal() -> None:
    # A cluster-DNS short name cannot resolve outside the cluster's search
    # domains — structurally internal, not a whitelist question. No fnmatch
    # glob can express "has no dot", so this rule lives in the engine rather
    # than in the pattern list.
    assert _category("opa:8181") == "internal"
    assert _category("ollama") == "internal"


def test_public_hosts_are_external() -> None:
    for host in [
        "api.openai.com",
        "partner.example.com:443",
        "exfil.attacker.net",
        "10.96.0.1",  # a raw dotted IP is not whitelisted -> external
    ]:
        assert _category(host) == "external", host


def test_pattern_must_match_on_label_boundary() -> None:
    # "evil-localtest.me" must not ride the "*.localtest.me" pattern: the glob
    # requires a literal dot before the whitelisted domain.
    assert _category("evil-localtest.me") == "external"
    # A subdomain of a bare-hostname entry needs its own "*." pattern.
    assert _category("x.localhost") == "internal"


def test_an_empty_whitelist_makes_every_dotted_host_external() -> None:
    # #178's safer default: absent configuration, nothing is trusted. The
    # dotless structural rule still applies.
    anchor = utils.AnchorFacts(direction="outbound", peer_host="records-tool.team2.svc")
    payload = utils.build_opa_input(**_BASE, anchor=anchor, internal_patterns=[])
    (dest,) = payload["data_destinations"]
    assert dest["data_destination_categories"] == ["external"]


# --- build_opa_input: the new fields -----------------------------------------

_BASE = dict(legs=[], span_ids=[], classifications={}, caller_entity_id=None,
             callee_entity_id=None)


def test_outbound_external_destination_maps_fully() -> None:
    anchor = utils.AnchorFacts(
        direction="outbound",
        peer_host="partner.example.com:443",
        self_id="priorauth-intake",
        url_scheme="https",
        url_path="/ingest",
    )
    payload = utils.build_opa_input(**_BASE, anchor=anchor, internal_patterns=_PATTERNS)
    (dest,) = payload["data_destinations"]
    assert dest == {
        "data_destination_name": "partner.example.com:443",
        "data_destination_categories": ["external"],
        "data_destination_url": "https://partner.example.com:443/ingest",
        "data_destination_trust_level": "UNTRUSTED_EXTERNAL",
    }
    assert payload["event_type"] == "external_sharing"
    assert "accessing_user" not in payload


def test_outbound_internal_destination_has_unknown_trust_level() -> None:
    """#178's follow-up stamps a trust level on every destination, derived
    from its category; the MVP whitelist has no richer category to draw a
    TRUSTED_* level from, so internal is stamped ``UNKNOWN`` rather than a
    guessed value (before that follow-up this branch omitted the key)."""
    anchor = utils.AnchorFacts(
        direction="outbound", peer_host="records-tool.team2.svc:8000",
        self_id="priorauth-intake", url_scheme="http", url_path="/mcp",
    )
    payload = utils.build_opa_input(**_BASE, anchor=anchor, internal_patterns=_PATTERNS)
    (dest,) = payload["data_destinations"]
    assert dest["data_destination_categories"] == ["internal"]
    assert dest["data_destination_trust_level"] == "UNKNOWN", (
        "internal carries no guessed TRUSTED_* value — the MVP trust model "
        "derives the level from the category alone"
    )
    assert payload["event_type"] == "internal_sharing"


def test_inbound_destination_is_self_and_principal_maps_to_user() -> None:
    anchor = utils.AnchorFacts(
        direction="inbound", peer_host="priorauth-intake.team2.svc:8000",
        self_id="priorauth-intake", principal_sub="alice",
    )
    payload = utils.build_opa_input(**_BASE, anchor=anchor, internal_patterns=_PATTERNS)
    (dest,) = payload["data_destinations"]
    assert dest["data_destination_name"] == "priorauth-intake"
    assert dest["data_destination_categories"] == ["internal"]
    assert "data_destination_url" not in dest, "no scheme fact -> no composed URL"
    assert payload["accessing_user"] == {"username": "alice", "user_roles": []}


def test_inbound_clusterip_reached_address_is_still_internal() -> None:
    """The live false-positive this pins: an inbound exchange whose
    `peer.host` is the raw ClusterIP the workload was reached on
    (10.96.x.x:8080) must NOT classify external — the destination of an
    inbound exchange is the workload itself, in-cluster by construction."""
    anchor = utils.AnchorFacts(
        direction="inbound", peer_host="10.96.47.165:8080",
        self_id="a2a-contact-extractor",
    )
    payload = utils.build_opa_input(**_BASE, anchor=anchor, internal_patterns=_PATTERNS)
    (dest,) = payload["data_destinations"]
    assert dest["data_destination_name"] == "a2a-contact-extractor"
    assert dest["data_destination_categories"] == ["internal"]
    assert dest["data_destination_trust_level"] == "UNKNOWN"
    assert payload["event_type"] == "internal_sharing"


def test_absent_anchor_omits_every_new_field() -> None:
    payload = utils.build_opa_input(**_BASE, anchor=None)
    for key in ("data_destinations", "event_type", "accessing_user"):
        assert key not in payload


def test_anchor_without_destination_fact_omits_destinations() -> None:
    anchor = utils.AnchorFacts(direction="outbound", principal_sub=None)
    payload = utils.build_opa_input(**_BASE, anchor=anchor, internal_patterns=_PATTERNS)
    assert "data_destinations" not in payload
    assert "event_type" not in payload


# --- fingerprint: the correctness trap ---------------------------------------


def test_changed_destination_changes_the_fingerprint() -> None:
    """The issue's explicit trap: if the anchor facts don't join the
    fingerprint, a changed destination never re-triggers OPA and the cached
    decision keeps answering for evidence it never saw."""
    legs = [utils.LegEvidence(leg_type="request", payload_hash="h1")]
    classifications = {"request": utils.PENDING}
    a = utils.AnchorFacts(direction="outbound", peer_host="records.team2.svc")
    b = utils.AnchorFacts(direction="outbound", peer_host="exfil.attacker.net")
    assert utils.fingerprint(legs, classifications, a) != utils.fingerprint(
        legs, classifications, b
    )


def test_changed_principal_changes_the_fingerprint() -> None:
    legs: list[utils.LegEvidence] = []
    a = utils.AnchorFacts(direction="inbound", principal_sub="alice")
    b = utils.AnchorFacts(direction="inbound", principal_sub="mallory")
    assert utils.fingerprint(legs, {}, a) != utils.fingerprint(legs, {}, b)


def test_absent_anchor_keeps_legacy_fingerprint_stable() -> None:
    """anchor=None (and an all-None anchor) fingerprint byte-identically to
    the pre-#163 form, so existing cached decisions for anchor-less evidence
    stay valid — no spurious OPA re-evaluation on deploy."""
    legs = [utils.LegEvidence(leg_type="request", payload_hash="h1")]
    classifications = {"request": utils.NO_PAYLOAD}
    legacy = json.dumps(
        {
            "legs_evidenced": utils.legs_evidenced(legs),
            "classification_summary": utils.classification_summary(classifications),
        },
        sort_keys=True,
        default=str,
    )
    assert utils.fingerprint(legs, classifications) == legacy
    assert utils.fingerprint(legs, classifications, utils.AnchorFacts()) == legacy


# --- gather_evidence reads the anchor span -----------------------------------

_TID = "trace-af-1"


def _insert_interaction(
    conn: psycopg.Connection,
    *,
    interaction_id: str,
    parent_interaction_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO interactions (id, trace_id, parent_interaction_id, "
        "caller_entity_id, callee_entity_id, summary) "
        "VALUES (%s, %s, %s, 'ent-a', 'ent-b', 'did a thing')",
        (interaction_id, _TID, parent_interaction_id),
    )


def _insert_anchor_span(
    conn: psycopg.Connection,
    *,
    interaction_id: str,
    span_id: str,
    attributes: dict,
) -> None:
    conn.execute(
        "INSERT INTO spans (trace_id, span_id, parent_id, kind, name, "
        "started_at, attributes, seq, arrival_seq, observed_at) "
        "VALUES (%s, %s, NULL, 'INTERNAL', 'call', now(), %s::jsonb, "
        "nextval('spans_seq'), currval('spans_seq'), now())",
        (_TID, span_id, json.dumps(attributes)),
    )
    conn.execute(
        "INSERT INTO interaction_spans (interaction_id, trace_id, span_id, "
        "role, leg_type) VALUES (%s, %s, %s, 'anchor', 'request')",
        (interaction_id, _TID, span_id),
    )


def test_gather_reads_anchor_facts_and_parent(configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn, interaction_id="ix-parent")
        _insert_interaction(
            conn, interaction_id="ix-anchored", parent_interaction_id="ix-parent"
        )
        _insert_anchor_span(
            conn,
            interaction_id="ix-anchored",
            span_id="span-req-1",
            attributes={
                "lineage.role": "request",
                "lineage.direction": "outbound",
                "lineage.peer.host": "partner.example.com:443",
                "lineage.self.id": "priorauth-intake",
                "url.scheme": "https",
                "url.path": "/ingest",
            },
        )
        conn.commit()

    evidence = gather_evidence("ix-anchored")
    assert evidence.parent_interaction_id == "ix-parent"
    assert evidence.anchor == utils.AnchorFacts(
        direction="outbound",
        peer_host="partner.example.com:443",
        self_id="priorauth-intake",
        url_scheme="https",
        url_path="/ingest",
    )


def test_gather_without_anchor_row_yields_none(configured_db: str) -> None:
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn, interaction_id="ix-bare")
        conn.commit()
    evidence = gather_evidence("ix-bare")
    assert evidence.anchor is None
    assert evidence.parent_interaction_id is None


def test_gather_non_sidecar_anchor_yields_none(configured_db: str) -> None:
    """An anchor row whose span carries no lineage.* facts (a non-sidecar
    derivation) reads as honest absence, not an empty facts object."""
    with psycopg.connect(configured_db) as conn:
        _insert_interaction(conn, interaction_id="ix-plain")
        _insert_anchor_span(
            conn,
            interaction_id="ix-plain",
            span_id="span-app-1",
            attributes={"http.method": "POST"},
        )
        conn.commit()
    assert gather_evidence("ix-plain").anchor is None
