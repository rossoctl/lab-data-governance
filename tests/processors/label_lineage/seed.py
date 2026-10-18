"""Loading a replayed lab trace (``tests/fixtures/label_lineage``) into a migrated
database, the way the interactions and classification processors would have
written it: entities, interactions, legs (their seq and wire time kept), payloads
and the classification verdicts."""

from __future__ import annotations

import json

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
