"""The memory scope of an entity (label lineage): the default by kind, the
declaration per entity natural key, and what the fold asks inside one conversation.
Data lineage's predicates do not read any of it."""

from __future__ import annotations

import dataclasses
import json

import pytest

from data_governance.label_lineage import fold
from data_governance.processors.data_lineage import memory
from data_governance.processors.data_lineage.traversal import Entity
from data_governance.processors.label_lineage import __main__ as label_lineage_main
from data_governance.processors.label_lineage import driver

KINDS = ("user", "client", "agent", "tool", "llm", "service")


def _e(kind: str, key: str | None = None) -> Entity:
    return Entity(id=kind, natural_key=key or f"{kind}:lab/{kind}", kind=kind)


@pytest.mark.parametrize(("kind", "want"), [
    ("llm", "call"), ("tool", "call"), ("user", "call"), ("client", "call"),
    ("agent", "conversation"), ("service", "persistent"),
])
def test_the_scope_by_kind(kind: str, want: str) -> None:
    assert memory.scope(_e(kind)) == want


def test_every_entity_kind_has_a_scope() -> None:
    assert set(memory.SCOPE_BY_KIND) == set(KINDS)
    assert set(memory.SCOPE_BY_KIND.values()) <= set(memory.SCOPES)


def test_an_unknown_kind_has_no_scope() -> None:
    with pytest.raises(ValueError, match="'store'"):
        memory.scope(_e("store"))


@pytest.mark.parametrize(("kind", "declared"), [
    ("service", "call"),          # a partner declared call scope: wider default, narrower declaration
    ("agent", "call"),
    ("tool", "persistent"),       # a tool declared a store: narrower default, wider declaration
    ("agent", "persistent"),
    ("llm", "conversation"),
])
def test_a_declaration_overrides_the_kind(kind: str, declared: str) -> None:
    entity = _e(kind)
    assert memory.scope(entity, {entity.natural_key: declared}) == declared
    assert memory.scope(entity, {"tool:lab/other": declared}) == memory.SCOPE_BY_KIND[kind]


def test_a_declaration_names_the_entity_by_its_natural_key_not_its_id() -> None:
    entity = Entity(id="e-uuid", natural_key="service:api.partner.example:8000", kind="service")
    assert memory.scope(entity, {"e-uuid": "call"}) == "persistent"
    assert memory.scope(entity, {"service:api.partner.example:8000": "call"}) == "call"


@pytest.mark.parametrize("kind", KINDS)
def test_inside_one_conversation_the_kind_decides_unless_declared(kind: str) -> None:
    """``mixes_calls``: by kind (``accumulates``) with no declaration and under
    ``persistent``; ``call`` and ``conversation`` say it themselves."""
    entity = _e(kind)
    assert memory.mixes_calls(entity) is memory.accumulates(entity)
    assert memory.mixes_calls(entity, {entity.natural_key: "persistent"}) is memory.accumulates(entity)
    assert memory.mixes_calls(entity, {entity.natural_key: "call"}) is False
    assert memory.mixes_calls(entity, {entity.natural_key: "conversation"}) is True


@pytest.mark.parametrize("kind", KINDS)
def test_data_lineage_predicates_are_untouched(kind: str) -> None:
    """The declaration is label lineage's: the data-lineage predicates take none."""
    entity = _e(kind)
    assert memory.accumulates(entity) is (kind == "agent")
    assert memory.is_entity_source(entity) is (kind == "tool")
    assert memory.is_entity_target(entity) is (kind == "tool")


# --- the declaration: LABEL_MEMORY_SCOPES, a JSON object -------------------------

@pytest.mark.parametrize("raw", [None, "", "  ", "{}"])
def test_nothing_declared(raw: str | None) -> None:
    assert memory.parse_scopes(raw) == {}


def test_a_declaration_takes_any_natural_key() -> None:
    raw = json.dumps({
        "service:api.v2.partner.example:8000": "call",
        "agent:lab/a": "persistent",
        "llm:host.containers.internal:11434/qwen2.5:7b": "call",
        "tool:a=b,c": "conversation",            # an = or a comma is part of the key, nothing else
    })
    assert memory.parse_scopes(raw) == {
        "service:api.v2.partner.example:8000": "call",
        "agent:lab/a": "persistent",
        "llm:host.containers.internal:11434/qwen2.5:7b": "call",
        "tool:a=b,c": "conversation",
    }


@pytest.mark.parametrize(("raw", "message"), [
    ('{"tool:lab/t2": "store"}', "'store'"),                    # an unknown scope
    ('{"tool:lab/t2": "Persistent"}', "'Persistent'"),          # scopes are not case-folded
    ('{"tool:lab/t2": 1}', "declares 1"),                       # not a string
    ('{"tool:lab/t2": null}', "declares None"),
    ('{"tool:lab/t2": ["call"]}', "declares \\['call'\\]"),
    ('{"": "call"}', "an empty natural key"),
    ("tool:lab/t2=call", "is not JSON"),                       # the old item form
    ('{"tool:lab/t2": "call",}', "is not JSON"),
    ('["tool:lab/t2", "call"]', "a JSON object"),               # not an object
    ('"call"', "a JSON object"),
    ('{"tool:lab/t2": "call", "tool:lab/t2": "call"}', "'tool:lab/t2' is given twice"),
    ('{"tool:lab/t2": "call", "tool:lab/t2": "persistent"}', "'tool:lab/t2' is given twice"),
    ('{"a": {"b": 1, "b": 2}}', "the key 'b' is given twice in one object"),   # at any depth, not a natural key
    ('{"tool:lab/t2\\nERROR forged": "store"}', "'tool:lab/t2\\\\nERROR forged' declares"),   # printed with !r
    ("[" * 100000, "nested too deeply"),                         # a RecursionError is not JSON either
    ('{"tool:lab/t2": ' + "1" * 5000 + "}", "is not JSON this parser can read: Exceeds the limit"),  # a ValueError
])
def test_an_invalid_declaration_fails_loudly(raw: str, message: str) -> None:
    with pytest.raises(memory.InvalidScopes, match=message):
        memory.parse_scopes(raw)


def test_the_processor_reads_the_declaration_from_its_environment(monkeypatch) -> None:
    monkeypatch.delenv(memory.SCOPES_ENV_VAR, raising=False)
    assert dict(driver.configured().scopes) == {}
    monkeypatch.setenv(memory.SCOPES_ENV_VAR, '{"service:api.v2.partner.example:8000": "call"}')
    assert dict(driver.configured().scopes) == {"service:api.v2.partner.example:8000": "call"}
    monkeypatch.setenv(memory.SCOPES_ENV_VAR, '{"service:api.v2.partner.example:8000": "forever"}')
    with pytest.raises(memory.InvalidScopes):
        driver.configured()


@pytest.mark.parametrize(("raw", "message"), [
    ('{"tool:lab/t2": "store"}', "'store'"),
    ('{"tool:lab/t2": ' + "1" * 5000 + "}", "Exceeds the limit"),   # json.loads raises a plain ValueError here
    ("[" * 100000, "nested too deeply"),
])
def test_an_invalid_declaration_stops_the_processor_before_any_db_work(monkeypatch, capsys, raw, message) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://nobody@127.0.0.1:1/none")
    monkeypatch.setenv(memory.SCOPES_ENV_VAR, raw)
    assert label_lineage_main.main() == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("cls", [driver.Config, fold.TraceInput, fold.WireLeg, memory.MemoryNode])
def test_dataclass_defaults_build_on_python_3_11(cls) -> None:
    """Python 3.11, the image's runtime, refuses a field default whose class is unhashable there (a dict, a
    mappingproxy); 3.12 hashes a mappingproxy and lets it through, so the venv cannot show it. A default here is an
    immutable scalar or empty tuple, or the field has a default_factory."""
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            assert f.default is None or type(f.default) in (str, int, float, bool, tuple, frozenset), (cls, f.name)
    assert driver.Config(None, "exact").scopes == {}


def test_data_lineage_ignores_a_declaration_in_the_environment(monkeypatch) -> None:
    """Data lineage's routing and derivation, called as its processor calls them, are the same with a declaration
    that would change label lineage on this trace (every entity declared against its kind)."""
    from data_governance.matching import get_matcher
    from data_governance.processors.data_lineage.traversal import derive_trace_lineage, inbound_sets
    from tests.label_lineage.fixtures import trace_input

    t = trace_input("R4")
    legs, entities = list(t.legs), dict(t.entities)

    def run():
        return (inbound_sets(sorted(legs, key=lambda leg: leg.seq), entities),
                derive_trace_lineage(legs, entities, matcher=get_matcher("simple"), payloads=dict(t.payloads)))

    monkeypatch.delenv(memory.SCOPES_ENV_VAR, raising=False)
    before = run()
    monkeypatch.setenv(memory.SCOPES_ENV_VAR, json.dumps(
        {e.natural_key: "call" if e.kind == "agent" else "conversation" for e in entities.values()}))
    assert run() == before
