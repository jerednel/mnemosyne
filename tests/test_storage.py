import sqlite3

import pytest

from mnemosyne.models import Alias, Entity, Memory, Relationship, new_id
from mnemosyne.resolution import normalize


def _entity(name="Threadron", entity_type="project", provenance_id="prov_x"):
    return Entity(
        id=new_id("usr_"),
        entity_type=entity_type,
        name=name,
        normalized_name=normalize(name),
        provenance_id=provenance_id,
    )


def test_canonical_is_read_only(canonical_store):
    with pytest.raises(sqlite3.OperationalError):
        canonical_store.conn.execute("INSERT INTO schema_meta (key, value) VALUES ('x', 'y')")


def test_canonical_seed_contents(canonical_store):
    databricks = canonical_store.get_entity("canon:company/databricks")
    assert databricks is not None
    assert databricks.entity_type == "company"
    aliases = {a.alias for a in canonical_store.aliases_for(databricks.id)}
    assert {"DBX", "the lakehouse vendor"} <= aliases
    rels = canonical_store.relationships_for(databricks.id)
    assert any(r.rel_type == "competitor_of" for r in rels)
    # Seed rows carry seed provenance and full assertion history.
    prov = canonical_store.get_provenance(databricks.provenance_id)
    assert prov.source_type == "seed"
    events = canonical_store.assertions_for(databricks.id)
    assert any(a.kind == "entity_created" for a in events)


def test_entity_roundtrip_with_assertion(overlay_store, provenance):
    entity = _entity(provenance_id=provenance.id)
    overlay_store.create_entity(entity, provenance)
    loaded = overlay_store.get_entity(entity.id)
    assert loaded == entity
    events = overlay_store.assertions_for(entity.id)
    assert [a.kind for a in events] == ["entity_created"]
    assert events[0].provenance_id == provenance.id
    assert overlay_store.get_provenance(provenance.id).assistant_id == "pytest/1.0"


def test_alias_roundtrip(overlay_store, provenance):
    entity = _entity(provenance_id=provenance.id)
    overlay_store.create_entity(entity, provenance)
    alias = Alias(
        entity_id=entity.id,
        alias="Thrd",
        normalized_alias=normalize("Thrd"),
        alias_type="abbreviation",
        provenance_id=provenance.id,
    )
    overlay_store.add_alias(alias, provenance)
    found = overlay_store.find_aliases("thrd")
    assert found[0].entity_id == entity.id
    assert any(a.kind == "alias_added" for a in overlay_store.assertions_for(alias.id))


def test_overlay_alias_may_reference_canonical_entity(overlay_store, provenance):
    # Learned spellings for shared entities live in the private overlay.
    alias = Alias(
        entity_id="canon:company/databricks",
        alias="Databrickz",
        normalized_alias=normalize("Databrickz"),
        provenance_id=provenance.id,
    )
    overlay_store.add_alias(alias, provenance)
    assert overlay_store.find_aliases("databrickz")[0].entity_id == "canon:company/databricks"


def test_supersede_relationship_atomic(overlay_store, provenance):
    old = Relationship(
        source_entity_id="usr_a",
        target_entity_id="usr_b",
        rel_type="works_at",
        provenance_id=provenance.id,
    )
    overlay_store.assert_relationship(old, provenance)
    new = Relationship(
        source_entity_id="usr_a",
        target_entity_id="usr_c",
        rel_type="works_at",
        provenance_id=provenance.id,
    )
    overlay_store.supersede_relationship(old.id, new, provenance)

    old_row = next(r for r in overlay_store.relationships_for("usr_a") if r.id == old.id)
    assert old_row.superseded_by == new.id
    assert old_row.valid_to == new.valid_from
    kinds = [a.kind for a in overlay_store.assertions_for(old.id)]
    assert kinds == ["relationship_asserted", "relationship_superseded"]


def test_memory_roundtrip_and_search(overlay_store, provenance):
    memory = Memory(
        content="Jeremy is migrating the lakehouse to Unity Catalog",
        memory_kind="fact",
        provenance_id=provenance.id,
        entity_ids=["canon:company/databricks"],
    )
    overlay_store.record_memory(memory, provenance)
    assert overlay_store.get_memory(memory.id).content == memory.content
    hits = overlay_store.search_memories("lakehouse")
    assert [m.id for m in hits] == [memory.id]
    assert overlay_store.memories_for_entity("canon:company/databricks")[0].id == memory.id
    assert not overlay_store.search_memories("kubernetes")


def test_search_handles_punctuation(overlay_store, provenance):
    memory = Memory(content="Weird query chars", provenance_id=provenance.id)
    overlay_store.record_memory(memory, provenance)
    # Must not raise FTS5 syntax errors.
    assert overlay_store.search_memories('weird "chars" AND OR *') is not None
    assert overlay_store.search_memories("!!!") == []
