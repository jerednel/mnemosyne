import pytest

from mnemosyne.ontology import OntologyError, OntologyRegistry


@pytest.fixture(scope="module")
def registry():
    return OntologyRegistry.load()


def test_types_load(registry):
    assert "person" in registry.entity_types
    assert "works_at" in registry.relationship_types
    assert registry.entity_types["company"].parent == "organization"


def test_subtype_walk(registry):
    assert registry.is_subtype("company", "organization")
    assert registry.is_subtype("company", "company")
    assert not registry.is_subtype("organization", "company")
    assert not registry.is_subtype("person", "organization")


def test_unknown_entity_type_rejected(registry):
    with pytest.raises(OntologyError, match="Unknown entity type"):
        registry.validate_entity_type("wizard")


def test_relationship_constraints(registry):
    registry.validate_relationship("works_at", "person", "company")  # company ⊂ organization
    with pytest.raises(OntologyError, match="requires source"):
        registry.validate_relationship("works_at", "technology", "company")
    with pytest.raises(OntologyError, match="requires target"):
        registry.validate_relationship("works_at", "person", "technology")
    with pytest.raises(OntologyError, match="Unknown relationship type"):
        registry.validate_relationship("teleports_to", "person", "location")


def test_wildcard_targets(registry):
    registry.validate_relationship("prefers", "person", "technology")
    registry.validate_relationship("prefers", "person", "concept")


def test_functional_flag(registry):
    assert registry.is_functional("works_at")
    assert not registry.is_functional("uses")


def test_describe_shape(registry):
    desc = registry.describe()
    names = {t["name"] for t in desc["entity_types"]}
    assert {"person", "company", "technology"} <= names
    works_at = next(r for r in desc["relationship_types"] if r["name"] == "works_at")
    assert works_at["functional"] is True
    assert works_at["source_types"] == ["person"]
