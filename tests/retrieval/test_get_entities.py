"""Tests for ``get_entities`` (issue #57 / ADR-0007).

Cursor-paginates on ``entity_id`` (the stable unique PK = sort axis = cursor
axis). Validates full-dataclass mapping, the ``semantic_kind`` filter, and the
keyset walk.
"""

from __future__ import annotations

import json

import pytest

from data_governance import retrieval


@pytest.fixture()
def insert_entity(raw_conn):
    def _ins(
        entity_id: str,
        *,
        service_name: str | None = None,
        semantic_kind: str = "UNKNOWN",
        sub_kind: str | None = None,
        display_name: str | None = None,
        attributes: dict | None = None,
    ) -> None:
        raw_conn.execute(
            "INSERT INTO entities (entity_id, service_name, semantic_kind, "
            "sub_kind, display_name, attributes, first_seen_at, last_seen_at) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, now(), now())",
            (
                entity_id,
                service_name,
                semantic_kind,
                sub_kind,
                display_name,
                json.dumps(attributes) if attributes is not None else None,
            ),
        )
        raw_conn.commit()

    return _ins


class TestGetEntitiesBasics:
    def test_maps_full_dataclass(self, configured_db, insert_entity) -> None:
        insert_entity(
            "id1",
            service_name="svc",
            semantic_kind="LLM",
            sub_kind="gpt-4",
            display_name="svc · LLM · gpt-4",
            attributes={"k": "v"},
        )
        result = retrieval.get_entities()
        assert len(result.entities) == 1
        e = result.entities[0]
        assert isinstance(e, retrieval.Entity)
        assert e.entity_id == "id1"
        assert e.service_name == "svc"
        assert e.semantic_kind == "LLM"
        assert e.sub_kind == "gpt-4"
        assert e.display_name == "svc · LLM · gpt-4"
        assert e.attributes == {"k": "v"}
        assert e.first_seen_at is not None
        assert e.last_seen_at is not None

    def test_null_optional_fields_map_to_none(
        self, configured_db, insert_entity
    ) -> None:
        # service_name / sub_kind / attributes may be NULL.
        insert_entity("id2", service_name=None, semantic_kind="UNKNOWN")
        e = retrieval.get_entities().entities[0]
        assert e.service_name is None
        assert e.sub_kind is None
        assert e.attributes is None

    def test_semantic_kind_filter(self, configured_db, insert_entity) -> None:
        insert_entity("a_llm", semantic_kind="LLM")
        insert_entity("b_tool", semantic_kind="TOOL")
        insert_entity("c_llm", semantic_kind="LLM")
        llms = retrieval.get_entities(semantic_kind="LLM")
        assert {e.entity_id for e in llms.entities} == {"a_llm", "c_llm"}


class TestGetEntitiesKeyset:
    def test_cursor_walk_on_entity_id_covers_all_once(
        self, configured_db, insert_entity
    ) -> None:
        ids = ["e1", "e2", "e3", "e4", "e5"]
        for i in ids:
            insert_entity(i, semantic_kind="UNKNOWN")

        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = retrieval.get_entities(cursor=cursor, limit=2)
            if not page.entities:
                break
            seen.extend(e.entity_id for e in page.entities)
            cursor = page.entities[-1].entity_id

        assert seen == ids  # sorted by entity_id, each exactly once

    def test_order_desc(self, configured_db, insert_entity) -> None:
        for i in ["e1", "e2", "e3"]:
            insert_entity(i, semantic_kind="UNKNOWN")
        desc = retrieval.get_entities(order="desc")
        assert [e.entity_id for e in desc.entities] == ["e3", "e2", "e1"]


class TestGetEntitiesValidation:
    def test_limit_over_cap_raises(self, configured_db) -> None:
        with pytest.raises(ValueError, match="hard cap"):
            retrieval.get_entities(limit=501)

    def test_bad_order_raises(self, configured_db) -> None:
        with pytest.raises(ValueError, match="order must be"):
            retrieval.get_entities(order="up")  # type: ignore[arg-type]
