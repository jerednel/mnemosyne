import hashlib


def _file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_merged_extends_view(fabric, provenance):
    fabric.create_entity(
        "Databricks",
        "company",
        provenance,
        summary="Our data platform vendor for the Threadron project.",
        attributes={"account_owner": "Jeremy", "founded": "should-not-win? no: overlay wins"},
        aliases=["that client"],
        extends="canon:company/databricks",
    )
    merged, tier = fabric.get_entity("canon:company/databricks")
    assert tier == "merged"
    # Overlay attributes win key-by-key; canonical keys not overridden survive.
    assert merged.attributes["account_owner"] == "Jeremy"
    assert merged.attributes["hq"] == "San Francisco, CA"
    assert merged.summary.startswith("Our data platform vendor")
    # Alias union spans tiers.
    aliases = fabric.aliases_of("canon:company/databricks")
    assert "DBX" in aliases and "that client" in aliases


def test_private_alias_resolves_to_canonical(fabric, provenance):
    ext = fabric.create_entity(
        "Databricks",
        "company",
        provenance,
        aliases=["that client"],
        extends="canon:company/databricks",
    )
    result = fabric.resolve_entity("that client", provenance)
    assert result.entity.id in (ext.id, "canon:company/databricks")


def test_extends_requires_canonical_target(fabric, provenance):
    import pytest

    from mnemosyne.fabric import FabricError

    with pytest.raises(FabricError, match="not found in the canonical tier"):
        fabric.create_entity("X", "company", provenance, extends="canon:company/nonexistent")


def test_graph_edges_tagged_by_tier(fabric, provenance):
    fabric.assert_relationship("Jeremy Nelson", "Databricks", "works_at", provenance)
    edges = fabric.query_graph("canon:company/databricks")
    tiers = {e.relationship.rel_type: e.tier for e in edges}
    assert tiers["works_at"] == "overlay"
    assert tiers["competitor_of"] == "canonical"


def test_graph_depth_two(fabric, provenance):
    fabric.assert_relationship("Jeremy Nelson", "Databricks", "works_at", provenance)
    resolved = fabric.resolve_entity("Jeremy Nelson", provenance)
    shallow = fabric.query_graph(resolved.entity.id, depth=1)
    deep = fabric.query_graph(resolved.entity.id, depth=2)
    assert len(deep) > len(shallow)
    # Depth 2 reaches canonical edges around Databricks.
    assert any(e.tier == "canonical" for e in deep)


def test_remember_and_recall(fabric, provenance):
    result = fabric.remember(
        "Jeremy is evaluating DBX for the lakehouse migration",
        provenance,
        mentions=["Jeremy Nelson", "DBX"],
        kind="fact",
    )
    linked_ids = {r.entity.id for r in result["linked_entities"]}
    assert "canon:company/databricks" in linked_ids

    by_text = fabric.recall("lakehouse migration", provenance)
    assert by_text["memories"][0]["memory"].id == result["memory"].id

    by_entity = fabric.recall("", provenance, entity="the lakehouse vendor")
    assert by_entity["memories"][0]["memory"].id == result["memory"].id
    assert "Databricks" in by_entity["memories"][0]["entities"]
    assert by_entity["memories"][0]["provenance"]["assistant_id"] == "pytest/1.0"


def test_relationship_creation_hint_types_endpoints(fabric, provenance):
    # works_at pins source to person: the auto-created endpoint must be a person,
    # otherwise ontology validation would reject the edge.
    result = fabric.assert_relationship("Jeremy Nelson", "Databricks", "works_at", provenance)
    assert result["source"].entity.entity_type == "person"
    assert result["target"].entity.id == "canon:company/databricks"


def test_no_canonical_leak(fabric, provenance, canonical_db):
    """After a full write workload, the canonical DB file is byte-identical."""
    before = _file_hash(canonical_db)
    fabric.create_entity("Secret Project", "project", provenance)
    fabric.remember("private note about DBX", provenance, mentions=["DBX", "Secret Project"])
    fabric.assert_relationship("Secret Project", "Unity Catalog", "depends_on", provenance)
    fabric.assert_relationship("Jeremy Nelson", "Anthropic", "works_at", provenance)
    fabric.resolve_entity("Databrics", provenance)  # learned alias write
    assert _file_hash(canonical_db) == before


def test_proposal_accept_adds_alias(fabric, provenance):
    result = fabric.resolve_entity("Datbrcks", provenance)
    assert result.status == "proposed"
    fabric.overlay.resolve_proposal(result.proposal_id, "accepted", provenance)
    from mnemosyne.models import Alias
    from mnemosyne.resolution import normalize

    fabric.overlay.add_alias(
        Alias(
            entity_id=result.entity.id,
            alias="Datbrcks",
            normalized_alias=normalize("Datbrcks"),
            provenance_id=provenance.id,
        ),
        provenance,
    )
    again = fabric.resolve_entity("Datbrcks", provenance)
    assert again.status == "matched"
    assert again.entity.id == "canon:company/databricks"
