from mnemosyne.temporal import is_current, is_valid_at


def _employer_at(fabric, provenance, person: str, at: str | None) -> set[str]:
    resolved = fabric.resolve_entity(person, provenance)
    edges = fabric.query_graph(
        resolved.entity.id,
        rel_type="works_at",
        direction="out",
        as_of=at,
        include_superseded=at is not None,
    )
    return {e.target_name for e in edges}


def test_functional_supersession(fabric, provenance):
    first = fabric.assert_relationship(
        "Jeremy Nelson",
        "Databricks",
        "works_at",
        provenance,
        valid_from="2024-01-01T00:00:00+00:00",
    )
    assert first["superseded"] == []

    second = fabric.assert_relationship(
        "Jeremy Nelson",
        "Anthropic",
        "works_at",
        provenance,
        valid_from="2025-06-01T00:00:00+00:00",
    )
    assert second["superseded"] == [first["relationship"].id]

    rels = {r.id: r for r in fabric.overlay.relationships_for(first["source"].entity.id)}
    old = rels[first["relationship"].id]
    new = rels[second["relationship"].id]
    assert old.superseded_by == new.id
    assert old.valid_to == new.valid_from
    assert not is_current(old)
    assert is_current(new)

    kinds = [a.kind for a in fabric.overlay.assertions_for(old.id)]
    assert kinds == ["relationship_asserted", "relationship_superseded"]


def test_as_of_returns_right_employer(fabric, provenance):
    fabric.assert_relationship(
        "Jeremy Nelson",
        "Databricks",
        "works_at",
        provenance,
        valid_from="2024-01-01T00:00:00+00:00",
    )
    fabric.assert_relationship(
        "Jeremy Nelson",
        "Anthropic",
        "works_at",
        provenance,
        valid_from="2025-06-01T00:00:00+00:00",
    )
    assert _employer_at(fabric, provenance, "Jeremy Nelson", "2024-07-01T00:00:00+00:00") == {
        "Databricks"
    }
    assert _employer_at(fabric, provenance, "Jeremy Nelson", "2026-01-01T00:00:00+00:00") == {
        "Anthropic"
    }
    # Before any employment: nothing.
    assert _employer_at(fabric, provenance, "Jeremy Nelson", "2020-01-01T00:00:00+00:00") == set()
    # Current view (no as_of) hides the superseded edge.
    assert _employer_at(fabric, provenance, "Jeremy Nelson", None) == {"Anthropic"}


def test_include_superseded_retrieval(fabric, provenance):
    fabric.assert_relationship("Jeremy Nelson", "Databricks", "works_at", provenance)
    fabric.assert_relationship("Jeremy Nelson", "Anthropic", "works_at", provenance)
    resolved = fabric.resolve_entity("Jeremy Nelson", provenance)
    all_edges = fabric.query_graph(resolved.entity.id, rel_type="works_at", include_superseded=True)
    assert {e.target_name for e in all_edges} == {"Databricks", "Anthropic"}


def test_non_functional_rels_accumulate(fabric, provenance):
    fabric.assert_relationship("Jeremy Nelson", "Python", "uses", provenance)
    result = fabric.assert_relationship("Jeremy Nelson", "Postgres", "uses", provenance)
    assert result["superseded"] == []
    resolved = fabric.resolve_entity("Jeremy Nelson", provenance)
    edges = fabric.query_graph(resolved.entity.id, rel_type="uses")
    assert {e.target_name for e in edges} == {"Python", "PostgreSQL"}


def test_explicit_supersedes(fabric, provenance):
    first = fabric.assert_relationship("Jeremy Nelson", "Python", "uses", provenance)
    second = fabric.assert_relationship(
        "Jeremy Nelson",
        "Apache Spark",
        "uses",
        provenance,
        supersedes=first["relationship"].id,
    )
    assert second["superseded"] == [first["relationship"].id]


def test_validity_predicates():
    class Fact:
        valid_from = "2024-01-01T00:00:00+00:00"
        valid_to = "2025-01-01T00:00:00+00:00"
        superseded_by = "rel_x"

    fact = Fact()
    assert is_valid_at(fact, "2024-06-01T00:00:00+00:00")
    assert not is_valid_at(fact, "2025-06-01T00:00:00+00:00")
    assert not is_valid_at(fact, "2023-06-01T00:00:00+00:00")
    assert not is_current(fact)


def test_timeline_shows_supersession(fabric, provenance):
    fabric.assert_relationship("Jeremy Nelson", "Databricks", "works_at", provenance)
    fabric.assert_relationship("Jeremy Nelson", "Anthropic", "works_at", provenance)
    resolved = fabric.resolve_entity("Jeremy Nelson", provenance)
    events = fabric.entity_timeline(resolved.entity.id)
    kinds = [e.kind for e in events]
    assert "entity_created" in kinds
    assert "relationship_superseded" in kinds
    superseded = next(e for e in events if e.kind == "relationship_superseded")
    assert superseded.provenance["source_type"] == "assistant"
    assert superseded.provenance["assistant_id"] == "pytest/1.0"
