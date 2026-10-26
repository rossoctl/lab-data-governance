"""Loading a replayed lab trace (``tests/fixtures/label_lineage``) into a migrated
database, the way the interactions and classification processors would have
written it: entities, interactions, legs (their seq and wire time kept), payloads
and the classification verdicts."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import psycopg
from psycopg.types.json import Jsonb

from tests.label_lineage.fixtures import load


def seed(dsn: str, row: str, *, classified: bool = True, skip_classification: frozenset[str] = frozenset()) -> dict:
    """Write fixture *row*; with ``classified=False`` no verdicts are written, and
    hashes in *skip_classification* are left unclassified. Returns the fixture."""
    d = load(row)
    kind_of = {p["content_hash"]: p["content_kind"] for p in d["payloads"]}
    with psycopg.connect(dsn) as conn:
        for e in d["entities"]:
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq, namespace) "
                "VALUES (%s, %s, %s, %s, 'observed', 1, %s) ON CONFLICT (natural_key) DO NOTHING",
                (e["id"], e["kind"], e["natural_key"], e["display_name"], e.get("namespace")),
            )
        for i in d["interactions"]:
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (i["id"], d["trace_id"], i["caller_entity_id"], i["callee_entity_id"], i["summary"]),
            )
        for p in d["payloads"]:
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES (%s, %s, %s) "
                "ON CONFLICT (content_hash) DO NOTHING",
                (p["content_hash"], Jsonb(p["content"]), len(json.dumps(p["content"]))),
            )
        for leg in d["legs"]:
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, seq, occurred_at, payload_hash, "
                "content_kind, error) VALUES (%s, %s, %s, %s, %s, %s, false)",
                (leg["interaction_id"], leg["leg_type"], leg["seq"], leg["occurred_at"], leg["payload_hash"],
                 kind_of.get(leg["payload_hash"])),
            )
        if classified:
            for c in d["classifications"]:
                if c["content_hash"] not in skip_classification:
                    classify(conn, c)
        conn.commit()
    return d


def classify(conn: psycopg.Connection, c: dict) -> None:
    conn.execute(
        "INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
        "contains_identity_bundle, is_personalized, primary_domain, findings, model_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (content_hash) DO NOTHING",
        (c["content_hash"], c["sensitivity_level"], c["regulatory_tags"], c["contains_identity_bundle"],
         c["is_personalized"], c["primary_domain"], Jsonb(c["findings"]), c["model_version"]),
    )


def seed_clone(dsn: str, row: str, *, trace_id: str, delta: dt.timedelta, seq_offset: int,
               classified: bool = True, skip_classification: frozenset[str] = frozenset()) -> dict:
    """Write fixture *row* again as ANOTHER trace: the same entities and payloads
    (shared rows), new interaction ids (uuid5 of the old id under *trace_id*),
    every leg's seq moved by *seq_offset* and its wire time by *delta*. Returns the
    rewritten fixture, so a test can name the clone's legs."""
    d = load(row)
    new_id = {i["id"]: str(uuid.uuid5(uuid.NAMESPACE_URL, f"{trace_id}/{i['id']}")) for i in d["interactions"]}
    d = {
        **d,
        "trace_id": trace_id,
        "interactions": [{**i, "id": new_id[i["id"]]} for i in d["interactions"]],
        "legs": [{**leg, "interaction_id": new_id[leg["interaction_id"]], "seq": leg["seq"] + seq_offset,
                  "occurred_at": (dt.datetime.fromisoformat(leg["occurred_at"]) + delta).isoformat()
                  if leg["occurred_at"] else None}
                 for leg in d["legs"]],
    }
    kind_of = {p["content_hash"]: p["content_kind"] for p in d["payloads"]}
    with psycopg.connect(dsn) as conn:
        for e in d["entities"]:
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq, namespace) "
                "VALUES (%s, %s, %s, %s, 'observed', 1, %s) ON CONFLICT (natural_key) DO NOTHING",
                (e["id"], e["kind"], e["natural_key"], e["display_name"], e.get("namespace")),
            )
        for i in d["interactions"]:
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (i["id"], d["trace_id"], i["caller_entity_id"], i["callee_entity_id"], i["summary"]),
            )
        for p in d["payloads"]:
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES (%s, %s, %s) "
                "ON CONFLICT (content_hash) DO NOTHING",
                (p["content_hash"], Jsonb(p["content"]), len(json.dumps(p["content"]))),
            )
        for leg in d["legs"]:
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, seq, occurred_at, payload_hash, "
                "content_kind, error) VALUES (%s, %s, %s, %s, %s, %s, false)",
                (leg["interaction_id"], leg["leg_type"], leg["seq"], leg["occurred_at"], leg["payload_hash"],
                 kind_of.get(leg["payload_hash"])),
            )
        if classified:
            for c in d["classifications"]:
                if c["content_hash"] not in skip_classification:
                    classify(conn, c)
        conn.commit()
    return d


def seed_trace(dsn: str, trace, *, seq_start: int, skip_classification: frozenset[str] = frozenset()) -> None:
    """Write a hand-built :class:`fold.TraceInput` (``tests/label_lineage/test_priors.py``'s
    shape) the way the processors would have: entities, interactions, legs numbered
    from *seq_start* in the order given, payloads, and a verdict for every payload
    the input holds findings for, and an empty verdict for every other payload (so the
    readiness cursor can pass every leg; hashes in *skip_classification* are left
    unclassified)."""
    with psycopg.connect(dsn) as conn:
        for e in trace.entities.values():
            conn.execute(
                "INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq) "
                "VALUES (%s, %s, %s, %s, 'observed', 1) ON CONFLICT (natural_key) DO NOTHING",
                (e.id, e.kind, e.natural_key, e.id),
            )
        for leg in trace.legs:
            conn.execute(
                "INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (leg.interaction_id, trace.trace_id, leg.caller_entity_id, leg.callee_entity_id, leg.interaction_id),
            )
        for h, content in trace.payloads.items():
            conn.execute(
                "INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES (%s, %s, %s) "
                "ON CONFLICT (content_hash) DO NOTHING",
                (h, Jsonb(content), len(json.dumps(content))),
            )
        for n, leg in enumerate(trace.legs):
            conn.execute(
                "INSERT INTO interaction_legs (interaction_id, leg_type, seq, occurred_at, payload_hash, "
                "content_kind, error) VALUES (%s, %s, %s, %s, %s, %s, false)",
                (leg.interaction_id, leg.leg_type, seq_start + n, leg.occurred_at, leg.payload_hash,
                 "tool_call_arguments" if leg.leg_type == "request" else "tool_call_result"),
            )
        for h in trace.payloads:
            if h in skip_classification:
                continue
            findings = list(trace.findings.get(h, ()))
            tags = sorted({t for f in findings for t in f.get("regulatory_tags", [])})
            conn.execute(
                "INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
                "contains_identity_bundle, is_personalized, primary_domain, findings, model_version) "
                "VALUES (%s, %s, %s, false, false, 'test', %s, 1) ON CONFLICT (content_hash) DO NOTHING",
                (h, "RESTRICTED" if findings else "PUBLIC", tags, Jsonb(list(findings))),
            )
        conn.commit()
